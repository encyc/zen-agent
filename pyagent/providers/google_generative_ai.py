"""Google Generative AI (``generateContent``) adapter.

Request side: ``contents`` with user/model roles, ``systemInstruction`` and
``functionDeclarations``. Stream side: SSE chunks carrying candidate parts;
``functionCall`` parts arrive complete, so a tool call is emitted in one step.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator

import httpx

from pyagent.events import AbortToken, AssistantEvent
from pyagent.providers.catalog import Model
from pyagent.providers.openai_completions import StreamOptions
from pyagent.providers.transport import default_client, iter_sse_data, post_stream
from pyagent.types import (
    AssistantMessage,
    ImageContent,
    Message,
    SystemMessage,
    TextContent,
    ToolCall,
    ToolDef,
    ToolResultMessage,
    Usage,
    UserMessage,
)

__all__ = ["build_request", "stream", "to_contents"]

_STOP_REASONS = {"STOP": "stop", "MAX_TOKENS": "length", "SAFETY": "stop", "OTHER": "stop"}


def _user_parts(content) -> list[dict]:
    if isinstance(content, str):
        return [{"text": content}]
    parts: list[dict] = []
    for block in content:
        if isinstance(block, TextContent):
            parts.append({"text": block.text})
        elif isinstance(block, ImageContent):
            parts.append(
                {"inlineData": {"mimeType": block.mime_type, "data": block.data}}
            )
    return parts


def to_contents(messages: list[Message]) -> tuple[str, list[dict]]:
    """Split the transcript into a system instruction and ``contents`` turns."""
    system_parts: list[str] = []
    contents: list[dict] = []

    def push(role: str, parts: list[dict]) -> None:
        if not parts:
            return
        if contents and contents[-1]["role"] == role:
            contents[-1]["parts"].extend(parts)
        else:
            contents.append({"role": role, "parts": parts})

    for message in messages:
        if isinstance(message, SystemMessage):
            system_parts.append(message.content)
        elif isinstance(message, UserMessage):
            push("user", _user_parts(message.content))
        elif isinstance(message, AssistantMessage):
            parts: list[dict] = []
            for block in message.content:
                if isinstance(block, TextContent):
                    if block.text:
                        parts.append({"text": block.text})
                elif isinstance(block, ToolCall):
                    parts.append(
                        {
                            "functionCall": {
                                "name": block.name,
                                "args": {} if block.arguments_error else block.arguments,
                            }
                        }
                    )
            push("model", parts)
        elif isinstance(message, ToolResultMessage):
            text = "\n".join(
                b.text for b in message.content if isinstance(b, TextContent)
            )
            push(
                "user",
                [
                    {
                        "functionResponse": {
                            "name": message.tool_name,
                            "response": {"content": text, "isError": message.is_error},
                        }
                    }
                ],
            )
    return "\n\n".join(part for part in system_parts if part), contents


def build_request(
    model: Model, messages: list[Message], tools: list[ToolDef], options: StreamOptions
) -> tuple[str, dict, dict]:
    system, contents = to_contents(messages)
    body: dict = {"contents": contents}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    if tools:
        body["tools"] = [
            {"functionDeclarations": [*(
                {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                }
                for tool in tools
            )]}
        ]
    generation: dict = {"maxOutputTokens": options.max_tokens or model.max_tokens}
    if options.temperature is not None:
        generation["temperature"] = options.temperature
    body["generationConfig"] = generation
    headers = {"x-goog-api-key": options.api_key, "content-type": "application/json"}
    if options.extra_headers:
        headers.update(options.extra_headers)
    url = f"{model.base_url.rstrip('/')}/models/{model.id}:streamGenerateContent?alt=sse"
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

    stop_reason = "stop"
    error_message: str | None = None

    async for chunk in chunks:
        if abort is not None and abort.aborted:
            partial.stop_reason = "aborted"
            yield AssistantEvent(type="error", partial=partial, reason="aborted")
            return
        if not isinstance(chunk, dict):
            continue
        prompt_feedback = chunk.get("promptFeedback") or {}
        if prompt_feedback.get("blockReason"):
            partial.stop_reason = "error"
            partial.error_message = f"blocked: {prompt_feedback['blockReason']}"
            yield AssistantEvent(type="error", partial=partial, reason="error")
            return
        usage = chunk.get("usageMetadata") or {}
        if usage:
            partial.usage.input = usage.get("promptTokenCount", partial.usage.input) or 0
            partial.usage.output = usage.get("candidatesTokenCount", partial.usage.output) or 0
            partial.usage.cache_read = usage.get("cachedContentTokenCount", 0) or 0
        for candidate in chunk.get("candidates") or []:
            content = candidate.get("content") or {}
            for part in content.get("parts") or []:
                text = part.get("text")
                if text:
                    if not any(isinstance(block, TextContent) for block in partial.content):
                        partial.content.append(TextContent(text=""))
                        yield AssistantEvent(
                            type="text_start",
                            partial=partial,
                            content_index=len(partial.content) - 1,
                        )
                    block = next(b for b in partial.content if isinstance(b, TextContent))
                    block.text += text
                    yield AssistantEvent(
                        type="text_delta",
                        partial=partial,
                        content_index=partial.content.index(block),
                        delta=text,
                    )
                function_call = part.get("functionCall")
                if isinstance(function_call, dict):
                    call = ToolCall(
                        id=f"call_{len(partial.content)}",
                        name=str(function_call.get("name") or ""),
                        arguments=function_call.get("args") or {},
                    )
                    partial.content.append(call)
                    index = len(partial.content) - 1
                    yield AssistantEvent(
                        type="toolcall_start", partial=partial, content_index=index, tool_call=call
                    )
                    yield AssistantEvent(
                        type="toolcall_end", partial=partial, content_index=index, tool_call=call
                    )
            reason = candidate.get("finishReason")
            if isinstance(reason, str):
                stop_reason = _STOP_REASONS.get(reason, "stop")

    has_calls = any(isinstance(block, ToolCall) for block in partial.content)
    partial.stop_reason = "tool_use" if has_calls else stop_reason
    if error_message is not None:
        partial.error_message = error_message
    yield AssistantEvent(
        type="done" if partial.stop_reason != "error" else "error",
        partial=partial,
        reason=partial.stop_reason,
    )


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
    url, headers, body = build_request(model, messages, tools, options)
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
