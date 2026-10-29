"""OpenAI Responses API adapter (third wire dialect behind StreamFn).

Request side: ``instructions`` carries the system prompt and ``input`` is a list
of items (messages plus function_call / function_call_output entries).
Stream side: ``response.*`` SSE events are folded into the unified event stream.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator

import httpx

from zenagent.events import AbortToken, AssistantEvent
from zenagent.providers.catalog import Model
from zenagent.providers.openai_completions import StreamOptions
from zenagent.providers.transport import default_client, iter_sse_data, post_stream
from zenagent.types import (
    AssistantMessage,
    ImageContent,
    Message,
    SystemMessage,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolDef,
    ToolResultMessage,
    Usage,
    UserMessage,
)

__all__ = ["build_params", "stream", "to_responses_input"]

_REASONING_EFFORTS = {"low", "medium", "high"}


def _user_parts(content) -> list[dict]:
    if isinstance(content, str):
        return [{"type": "input_text", "text": content}]
    parts: list[dict] = []
    for block in content:
        if isinstance(block, TextContent):
            parts.append({"type": "input_text", "text": block.text})
        elif isinstance(block, ImageContent):
            parts.append(
                {
                    "type": "input_image",
                    "image_url": f"data:{block.mime_type};base64,{block.data}",
                }
            )
    return parts


def to_responses_input(messages: list[Message]) -> tuple[str, list[dict]]:
    """Split the transcript into ``instructions`` plus Responses input items."""
    instructions: list[str] = []
    items: list[dict] = []
    for message in messages:
        if isinstance(message, SystemMessage):
            instructions.append(message.content)
        elif isinstance(message, UserMessage):
            items.append({"role": "user", "content": _user_parts(message.content)})
        elif isinstance(message, AssistantMessage):
            texts = [b.text for b in message.content if isinstance(b, TextContent) if b.text]
            if texts:
                items.append(
                    {
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "\n".join(texts)}],
                    }
                )
            for block in message.content:
                if isinstance(block, ToolCall):
                    items.append(
                        {
                            "type": "function_call",
                            "call_id": block.id,
                            "name": block.name,
                            "arguments": json.dumps(
                                block.arguments if not block.arguments_error else {}
                            ),
                        }
                    )
        elif isinstance(message, ToolResultMessage):
            text = "\n".join(
                b.text for b in message.content if isinstance(b, TextContent)
            )
            items.append(
                {"type": "function_call_output", "call_id": message.tool_call_id, "output": text}
            )
    return "\n\n".join(part for part in instructions if part), items


def build_params(
    model: Model, messages: list[Message], tools: list[ToolDef], options: StreamOptions
) -> tuple[str, dict, dict]:
    instructions, items = to_responses_input(messages)
    body: dict = {
        "model": model.id,
        "input": items,
        "stream": True,
        "max_output_tokens": options.max_tokens or model.max_tokens,
    }
    if instructions:
        body["instructions"] = instructions
    if tools:
        body["tools"] = [
            {
                "type": "function",
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            }
            for tool in tools
        ]
    if options.thinking in _REASONING_EFFORTS:
        body["reasoning"] = {"effort": options.thinking}
    headers = {
        "Authorization": f"Bearer {options.api_key}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    if options.extra_headers:
        headers.update(options.extra_headers)
    if options.session_id:
        headers["x-opencode-session"] = options.session_id
    url = f"{model.base_url.rstrip('/')}/responses"
    return url, headers, body


async def _events_from_chunks(
    model: Model, chunks: AsyncIterator[dict], abort: AbortToken | None = None
) -> AsyncIterator[AssistantEvent]:
    partial = AssistantMessage(
        content=[],
        provider=model.provider,
        model=model.id,
        usage=Usage(),
        stop_reason="pending",
        timestamp=int(time.time()),
    )
    yield AssistantEvent(type="start", partial=partial)

    calls: dict[str, ToolCall] = {}
    completed_status = "completed"

    async for chunk in chunks:
        if abort is not None and abort.aborted:
            partial.stop_reason = "aborted"
            yield AssistantEvent(type="error", partial=partial, reason="aborted")
            return
        kind = chunk.get("type")

        if kind == "response.created":
            response = chunk.get("response") or {}
            if isinstance(response.get("id"), str):
                partial.response_id = response["id"]

        elif kind == "response.output_item.added":
            item = chunk.get("item") or {}
            if item.get("type") == "function_call":
                call = ToolCall(
                    id=str(item.get("call_id") or item.get("id") or ""),
                    name=str(item.get("name") or ""),
                    arguments={},
                )
                calls[call.id] = call
                partial.content.append(call)
                yield AssistantEvent(
                    type="toolcall_start",
                    partial=partial,
                    content_index=len(partial.content) - 1,
                    tool_call=call,
                )

        elif kind == "response.output_text.delta":
            delta = chunk.get("delta") or ""
            if delta:
                if not any(isinstance(block, TextContent) for block in partial.content):
                    partial.content.append(TextContent(text=""))
                    yield AssistantEvent(
                        type="text_start",
                        partial=partial,
                        content_index=len(partial.content) - 1,
                    )
                block = next(b for b in partial.content if isinstance(b, TextContent))
                block.text += delta
                yield AssistantEvent(
                    type="text_delta",
                    partial=partial,
                    content_index=partial.content.index(block),
                    delta=delta,
                )

        elif kind == "response.function_call_arguments.delta":
            item_id = str(chunk.get("item_id") or "")
            call = calls.get(item_id)
            if call is not None:
                fragment = chunk.get("delta") or ""
                call.arguments_error = (call.arguments_error or "") + fragment
                yield AssistantEvent(
                    type="toolcall_delta",
                    partial=partial,
                    content_index=partial.content.index(call),
                    delta=fragment,
                    tool_call=call,
                )

        elif kind == "response.output_item.done":
            item = chunk.get("item") or {}
            if item.get("type") == "function_call":
                call = calls.get(str(item.get("call_id") or item.get("id") or ""))
                if call is not None:
                    try:
                        call.arguments = json.loads(item.get("arguments") or "{}")
                        call.arguments_error = None
                    except ValueError:
                        call.arguments = {}
                        call.arguments_error = "invalid JSON arguments"
                    yield AssistantEvent(
                        type="toolcall_end",
                        partial=partial,
                        content_index=partial.content.index(call),
                        tool_call=call,
                    )

        elif kind == "response.completed":
            response = chunk.get("response") or {}
            completed_status = str(response.get("status") or "completed")
            usage = response.get("usage") or {}
            partial.usage.input = usage.get("input_tokens", 0) or 0
            partial.usage.output = usage.get("output_tokens", 0) or 0
            details = usage.get("input_tokens_details") or {}
            partial.usage.cache_read = details.get("cached_tokens", 0) or 0

        elif kind in ("response.failed", "error"):
            response = chunk.get("response") or {}
            error = response.get("error") or chunk.get("error") or {}
            partial.stop_reason = "error"
            partial.error_message = str(error.get("message") or "provider error")
            yield AssistantEvent(type="error", partial=partial, reason="error")
            return

    for block in partial.content:
        if isinstance(block, ThinkingContent):
            yield AssistantEvent(
                type="thinking_end", partial=partial, content_index=partial.content.index(block)
            )

    has_calls = any(isinstance(block, ToolCall) for block in partial.content)
    if partial.stop_reason == "pending":
        if completed_status == "incomplete" and not has_calls:
            partial.stop_reason = "length"
        else:
            partial.stop_reason = "tool_use" if has_calls else "stop"
    yield AssistantEvent(type="done", partial=partial, reason=partial.stop_reason)


async def _chunk_stream(response: httpx.Response) -> AsyncIterator[dict]:
    async def lines() -> AsyncIterator[str]:
        async for line in response.aiter_lines():
            yield f"{line}\n"

    async for payload in iter_sse_data(lines()):
        try:
            yield json.loads(payload)
        except ValueError:
            continue


async def stream(
    model: Model, messages: list[Message], tools: list[ToolDef], options: StreamOptions
) -> AsyncIterator[AssistantEvent]:
    url, headers, body = build_params(model, messages, tools, options)
    client = default_client()
    try:
        response = await post_stream(client, url, headers, body, abort=options.abort)
        try:
            async for event in _events_from_chunks(
                model, _chunk_stream(response), options.abort
            ):
                yield event
        finally:
            await response.aclose()
    finally:
        await client.aclose()
