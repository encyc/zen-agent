"""Anthropic Messages API adapter.

Second protocol behind the same ``StreamFn`` contract as the OpenAI-compatible
adapter, so the registry/loop/UI never learn which wire format they are talking
to. Differences it owns:

* request: top-level ``system``, ``tool_use``/``tool_result`` content blocks,
  ``tools[].input_schema``, and a token *budget* (not a level name) for thinking.
* response: ``content_block_start/delta/stop`` events, ``signature_delta`` for
  thinking round-tripping, ``input_json_delta`` for tool arguments, and a final
  ``message_delta`` carrying ``stop_reason`` and output usage.

Same guarantees as the other adapter: ``stream`` never raises (``safe_stream``
turns failures into terminal events) and every event carries the live partial
assistant message.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx

from pyagent.events import AbortToken, AssistantEvent
from pyagent.providers.catalog import Model
from pyagent.providers.compat import normalize_thinking
from pyagent.providers.openai_completions import StreamOptions
from pyagent.providers.transport import default_client, iter_sse_data, post_stream
from pyagent.types import (
    AssistantMessage,
    Cost,
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

__all__ = [
    "ANTHROPIC_VERSION",
    "build_request",
    "stream",
    "thinking_budget",
    "to_anthropic_messages",
]

#: Pinned so behaviour does not drift when the API adds fields.
ANTHROPIC_VERSION = "2023-06-01"

#: Token budgets per user-level thinking setting.
_THINKING_BUDGETS = {"low": 4096, "medium": 16384, "high": 32768}

#: Anthropic stop_reason -> unified StopReason.
_STOP_MAP = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "tool_use": "tool_use",
    "max_tokens": "length",
    "refusal": "stop",
    "pause_turn": "stop",
}


def thinking_budget(level: str | None, max_tokens: int) -> int | None:
    """Budget for a thinking level, kept strictly below ``max_tokens``."""
    if level is None:
        return None
    if level not in _THINKING_BUDGETS:
        raise ValueError(
            f"invalid thinking level {level!r}; expected one of "
            "None, 'off', 'low', 'medium', 'high'"
        )
    cap = max(1024, max_tokens // 2)
    return min(_THINKING_BUDGETS[level], cap)


# ---------------------------------------------------------------------------
# Request side
# ---------------------------------------------------------------------------


def _user_blocks(content) -> list[dict]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    blocks: list[dict] = []
    for block in content:
        if isinstance(block, TextContent):
            blocks.append({"type": "text", "text": block.text})
        elif isinstance(block, ImageContent):
            blocks.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": block.mime_type,
                        "data": block.data,
                    },
                }
            )
    return blocks


def _assistant_blocks(message: AssistantMessage) -> list[dict]:
    blocks: list[dict] = []
    for block in message.content:
        if isinstance(block, TextContent):
            if block.text:
                blocks.append({"type": "text", "text": block.text})
        elif isinstance(block, ThinkingContent):
            if block.redacted:
                blocks.append({"type": "redacted_thinking", "data": block.signature or ""})
            else:
                entry: dict = {"type": "thinking", "thinking": block.thinking}
                if block.signature:
                    entry["signature"] = block.signature
                blocks.append(entry)
        elif isinstance(block, ToolCall):
            blocks.append(
                {
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": {} if block.arguments_error else block.arguments,
                }
            )
    return blocks


def _tool_result_blocks(message: ToolResultMessage) -> list[dict]:
    content: list[dict] = []
    for block in message.content:
        if isinstance(block, TextContent):
            content.append({"type": "text", "text": block.text})
        elif isinstance(block, ImageContent):
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": block.mime_type,
                        "data": block.data,
                    },
                }
            )
    entry: dict = {
        "type": "tool_result",
        "tool_use_id": message.tool_call_id,
        "content": content or [{"type": "text", "text": ""}],
    }
    if message.is_error:
        entry["is_error"] = True
    return [entry]


def to_anthropic_messages(messages: list[Message]) -> tuple[str, list[dict]]:
    """Split the transcript into a system prompt and Messages-API turns.

    Consecutive same-role turns are merged into one message: the Messages API
    requires alternating roles, and several tool results in a row are normal.
    """
    system_parts: list[str] = []
    turns: list[dict] = []

    def push(role: str, blocks: list[dict]) -> None:
        if not blocks:
            return
        if turns and turns[-1]["role"] == role:
            turns[-1]["content"].extend(blocks)
        else:
            turns.append({"role": role, "content": blocks})

    for message in messages:
        if isinstance(message, SystemMessage):
            system_parts.append(message.content)
        elif isinstance(message, UserMessage):
            push("user", _user_blocks(message.content))
        elif isinstance(message, AssistantMessage):
            push("assistant", _assistant_blocks(message))
        elif isinstance(message, ToolResultMessage):
            push("user", _tool_result_blocks(message))
    return "\n\n".join(part for part in system_parts if part), turns


def build_request(
    model: Model,
    messages: list[Message],
    tools: list[ToolDef],
    options: StreamOptions,
) -> tuple[str, dict, dict]:
    """Return ``(url, headers, body)`` for one streaming Messages request."""
    system, turns = to_anthropic_messages(messages)
    max_tokens = options.max_tokens or model.max_tokens
    level = normalize_thinking(options.thinking)

    body: dict = {
        "model": model.id,
        "max_tokens": max_tokens,
        "stream": True,
        "messages": turns,
    }
    if system:
        body["system"] = system
    if tools:
        body["tools"] = [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.parameters,
            }
            for tool in tools
        ]
    budget = thinking_budget(level, max_tokens)
    if budget is not None:
        if model.compat.anthropic_adaptive_thinking:
            # Newer Anthropic shape: the model decides how much to think, the
            # caller only picks an effort level.
            body["thinking"] = {"type": "adaptive"}
            body["output_config"] = {"effort": level}
        else:
            body["thinking"] = {"type": "enabled", "budget_tokens": budget}
    elif options.temperature is not None:
        # Anthropic rejects temperature together with extended thinking.
        body["temperature"] = options.temperature

    headers = {
        "x-api-key": options.api_key,
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
        "accept": "text/event-stream",
    }
    if options.extra_headers:
        headers.update(options.extra_headers)
    if options.session_id:
        headers["x-opencode-session"] = options.session_id
    base = model.base_url.rstrip("/")
    # Anthropic SDK convention: the caller's base URL does not include /v1, so
    # the version prefix is ours to add (pi-ai derives its base the same way).
    url = f"{base}/messages" if base.endswith("/v1") else f"{base}/v1/messages"
    return url, headers, body


# ---------------------------------------------------------------------------
# Stream side: SSE events -> AssistantEvent
# ---------------------------------------------------------------------------


@dataclass
class _Block:
    """One content block being assembled from the event stream."""

    index: int
    kind: str  # "text" | "thinking" | "tool_use"
    block: TextContent | ThinkingContent | ToolCall
    arguments_text: str = ""


def _finalize_tool_arguments(entry: _Block) -> None:
    call = entry.block
    assert isinstance(call, ToolCall)
    if not entry.arguments_text:
        return
    try:
        arguments = json.loads(entry.arguments_text)
    except json.JSONDecodeError:
        call.arguments = {}
        call.arguments_error = f"invalid JSON arguments: {entry.arguments_text[:200]}"
        return
    if not isinstance(arguments, dict):
        call.arguments = {}
        call.arguments_error = (
            f"tool arguments are not a JSON object: {entry.arguments_text[:200]}"
        )
        return
    call.arguments = arguments


def _with_cost(partial: AssistantMessage, model: Model) -> None:
    usage = partial.usage
    cost_in = usage.input * model.cost.input / 1_000_000
    cost_out = usage.output * model.cost.output / 1_000_000
    usage.cost = Cost(input=cost_in, output=cost_out, total=cost_in + cost_out)


async def _events_from_chunks(
    model: Model,
    chunks: AsyncIterator[dict],
    abort: AbortToken | None = None,
) -> AsyncIterator[AssistantEvent]:
    """Assemble Messages API events into the unified event stream.

    This is the pure parsing core; :func:`stream` only does HTTP.
    """
    partial = AssistantMessage(
        content=[],
        provider=model.provider,
        model=model.id,
        usage=Usage(),
        stop_reason="pending",
        timestamp=int(time.time()),
    )
    yield AssistantEvent(type="start", partial=partial)

    entries: dict[int, _Block] = {}
    stop_reason = "stop"
    error_message: str | None = None

    async for chunk in chunks:
        if abort is not None and abort.aborted:
            partial.stop_reason = "aborted"
            yield AssistantEvent(type="error", partial=partial, reason="aborted")
            return
        if not isinstance(chunk, dict):
            continue
        kind = chunk.get("type")

        if kind == "message_start":
            message = chunk.get("message") or {}
            if isinstance(message.get("id"), str):
                partial.response_id = message["id"]
            usage = message.get("usage") or {}
            partial.usage.input = usage.get("input_tokens", 0) or 0
            partial.usage.cache_read = usage.get("cache_read_input_tokens", 0) or 0
            partial.usage.cache_write = usage.get("cache_creation_input_tokens", 0) or 0

        elif kind == "content_block_start":
            index = chunk.get("index", len(entries))
            block = chunk.get("content_block") or {}
            block_type = block.get("type")
            if block_type == "text":
                entry = _Block(index, "text", TextContent(text=block.get("text", "")))
                partial.content.append(entry.block)
                entries[index] = entry
                yield AssistantEvent(
                    type="text_start", partial=partial, content_index=len(partial.content) - 1
                )
            elif block_type == "thinking":
                entry = _Block(
                    index,
                    "thinking",
                    ThinkingContent(
                        thinking=block.get("thinking", ""),
                        signature=block.get("signature"),
                    ),
                )
                partial.content.append(entry.block)
                entries[index] = entry
                yield AssistantEvent(
                    type="thinking_start",
                    partial=partial,
                    content_index=len(partial.content) - 1,
                )
            elif block_type == "redacted_thinking":
                entry = _Block(
                    index,
                    "thinking",
                    ThinkingContent(
                        thinking="", signature=block.get("data"), redacted=True
                    ),
                )
                partial.content.append(entry.block)
                entries[index] = entry
                yield AssistantEvent(
                    type="thinking_start",
                    partial=partial,
                    content_index=len(partial.content) - 1,
                )
            elif block_type == "tool_use":
                entry = _Block(
                    index,
                    "tool_use",
                    ToolCall(
                        id=block.get("id", f"tool_{index}"),
                        name=block.get("name", ""),
                        arguments={},
                    ),
                )
                partial.content.append(entry.block)
                entries[index] = entry
                yield AssistantEvent(
                    type="toolcall_start",
                    partial=partial,
                    content_index=len(partial.content) - 1,
                    tool_call=entry.block,
                )

        elif kind == "content_block_delta":
            entry = entries.get(chunk.get("index"))
            if entry is None:
                continue
            delta = chunk.get("delta") or {}
            delta_type = delta.get("type")
            if delta_type == "text_delta" and isinstance(entry.block, TextContent):
                entry.block.text += delta.get("text", "")
                yield AssistantEvent(
                    type="text_delta",
                    partial=partial,
                    content_index=partial.content.index(entry.block),
                    delta=delta.get("text", ""),
                )
            elif delta_type == "thinking_delta" and isinstance(
                entry.block, ThinkingContent
            ):
                entry.block.thinking += delta.get("thinking", "")
                yield AssistantEvent(
                    type="thinking_delta",
                    partial=partial,
                    content_index=partial.content.index(entry.block),
                    delta=delta.get("thinking", ""),
                )
            elif delta_type == "signature_delta" and isinstance(
                entry.block, ThinkingContent
            ):
                entry.block.signature = delta.get("signature")
            elif delta_type == "input_json_delta" and isinstance(entry.block, ToolCall):
                fragment = delta.get("partial_json", "")
                entry.arguments_text += fragment
                yield AssistantEvent(
                    type="toolcall_delta",
                    partial=partial,
                    content_index=partial.content.index(entry.block),
                    delta=fragment,
                    tool_call=entry.block,
                )

        elif kind == "content_block_stop":
            entry = entries.get(chunk.get("index"))
            if entry is None:
                continue
            position = partial.content.index(entry.block)
            if entry.kind == "text":
                yield AssistantEvent(
                    type="text_end",
                    partial=partial,
                    content_index=position,
                    content=entry.block.text,  # type: ignore[union-attr]
                )
            elif entry.kind == "thinking":
                yield AssistantEvent(
                    type="thinking_end",
                    partial=partial,
                    content_index=position,
                    content=entry.block.thinking,  # type: ignore[union-attr]
                )
            else:
                _finalize_tool_arguments(entry)
                yield AssistantEvent(
                    type="toolcall_end",
                    partial=partial,
                    content_index=position,
                    tool_call=entry.block,
                )

        elif kind == "message_delta":
            raw_stop = (chunk.get("delta") or {}).get("stop_reason")
            if isinstance(raw_stop, str):
                mapped = _STOP_MAP.get(raw_stop)
                if mapped is None:
                    stop_reason = "error"
                    error_message = f"unknown stop_reason: {raw_stop!r}"
                else:
                    stop_reason = mapped
            usage = chunk.get("usage") or {}
            if "output_tokens" in usage:
                partial.usage.output = usage.get("output_tokens", 0) or 0

        elif kind == "error":
            detail = chunk.get("error") or {}
            stop_reason = "error"
            error_message = str(detail.get("message") or detail or "provider error")

        elif kind == "message_stop":
            break

    # Close blocks that never got an explicit stop event (defensive).
    for entry in entries.values():
        if entry.kind == "tool_use" and not entry.arguments_text:
            continue
        if entry.kind == "tool_use":
            _finalize_tool_arguments(entry)

    _with_cost(partial, model)
    partial.stop_reason = stop_reason  # type: ignore[assignment]
    if error_message is not None:
        partial.error_message = error_message
    yield AssistantEvent(
        type="done" if stop_reason != "error" else "error",
        partial=partial,
        reason=stop_reason,  # type: ignore[arg-type]
    )


async def _chunk_stream(response: httpx.Response) -> AsyncIterator[dict]:
    """Parse SSE data payloads from a streaming response into dicts."""

    async def lines() -> AsyncIterator[str]:
        async for line in response.aiter_lines():
            yield f"{line}\n"

    async for payload in iter_sse_data(lines()):
        try:
            yield json.loads(payload)
        except ValueError:
            continue


async def stream(
    model: Model,
    messages: list[Message],
    tools: list[ToolDef],
    options: StreamOptions,
) -> AsyncIterator[AssistantEvent]:
    """Stream one Messages API completion; failures surface as terminal events."""
    url, headers, body = build_request(model, messages, tools, options)
    client = default_client()
    try:
        response = await post_stream(
            client, url, headers, body, abort=options.abort
        )
        try:
            async for event in _events_from_chunks(
                model, _chunk_stream(response), options.abort
            ):
                yield event
        finally:
            await response.aclose()
    finally:
        await client.aclose()
