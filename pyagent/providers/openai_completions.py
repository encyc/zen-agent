"""OpenAI-compatible chat-completions adapter.

Speaks the OpenAI ``/chat/completions`` wire dialect for every catalog
model whose ``api`` is ``openai-completions`` (plain OpenAI-compatible,
Z.AI GLM coding, DeepSeek, the OpenCode gateway …). Dialect quirks are
carried by :class:`~pyagent.providers.compat.CompatFlags`; this module
never looks at base_urls.

Two halves, both free of network code — the shared httpx/SSE transport
in ``transport.py`` does the I/O:

* Request side: ``to_request_messages`` maps the unified message model
  onto OpenAI message JSON, ``build_params`` assembles the request body
  from a catalog ``Model`` + ``StreamOptions`` under a dialect's compat
  flags (messages themselves are merged by the caller).
* Stream side: ``stream`` is the single public entry (one ``post_stream``
  call, SSE data payloads → parsed chunks → events); the pure chunk→event
  delta assembly lives in ``_events_from_chunks``, which the offline
  fixture tests drive directly, without HTTP.
"""

import json
import time
from collections.abc import AsyncIterable, AsyncIterator
from dataclasses import dataclass

import httpx

from pyagent.events import AbortToken, AssistantEvent
from pyagent.providers.catalog import Model
from pyagent.providers.compat import CompatFlags, normalize_thinking
from pyagent.providers.transport import (
    default_client,
    iter_sse_data,
    parse_usage,
    post_stream,
)
from pyagent.types import (
    AssistantMessage,
    Message,
    StopReason,
    SystemMessage,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolDef,
    Usage,
    UserMessage,
)

__all__ = ["StreamOptions", "build_params", "stream", "to_request_messages"]


@dataclass
class StreamOptions:
    """Per-call options for one streaming completion request.

    ``thinking`` is the user-level reasoning setting ("off"/"low"/
    "medium"/"high"); the request side translates it into each dialect's
    ``thinking`` block / bare ``reasoning_effort``. ``abort`` is the
    cooperative cancellation token shared with the loop and transport.
    """

    api_key: str = ""
    thinking: str | None = None
    max_tokens: int | None = None
    temperature: float | None = None
    session_id: str | None = None
    extra_headers: dict | None = None
    abort: AbortToken | None = None


# ---------------------------------------------------------------------------
# Request side
# ---------------------------------------------------------------------------


def _user_content(content: str | list) -> str | list[dict]:
    """User content: plain string, or OpenAI multimodal content parts."""
    if isinstance(content, str):
        return content
    parts: list[dict] = []
    for block in content:
        if isinstance(block, TextContent):
            parts.append({"type": "text", "text": block.text})
        else:
            parts.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{block.mime_type};base64,{block.data}"},
                }
            )
    return parts


def _assistant_request_message(message: AssistantMessage, compat: CompatFlags) -> dict:
    """Split assistant content blocks into text / tool_calls / reasoning.

    ``content`` is the newline-joined text (None when the turn carries no
    text — e.g. thinking- or tool-call-only turns). Tool arguments are
    serialized to a JSON string; a ToolCall whose arguments failed to
    parse degrades to ``"{}"`` so the request stays well-formed. DeepSeek
    reasoner additionally requires the thinking text echoed back as
    ``reasoning_content`` on assistant turns.
    """
    texts = [b.text for b in message.content if isinstance(b, TextContent)]
    thinking = [b.thinking for b in message.content if isinstance(b, ThinkingContent)]
    calls = [b for b in message.content if isinstance(b, ToolCall)]

    request: dict = {
        "role": "assistant",
        "content": "\n".join(texts) if texts else None,
    }
    if calls:
        request["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": (
                        "{}"
                        if call.arguments_error is not None
                        else json.dumps(call.arguments)
                    ),
                },
            }
            for call in calls
        ]
    if compat.requires_reasoning_content_on_assistant and thinking:
        request["reasoning_content"] = "\n".join(thinking)
    return request


def to_request_messages(messages: list[Message], compat: CompatFlags) -> list[dict]:
    """Map unified messages onto OpenAI chat-completions message JSON.

    Timestamps and bookkeeping fields (provider, usage, is_error, …) are
    dropped: the wire format carries only what the API needs. Tools do
    not appear here — they ride in the request params via ``build_params``.
    """
    request: list[dict] = []
    for message in messages:
        if isinstance(message, SystemMessage):
            request.append({"role": "system", "content": message.content})
        elif isinstance(message, UserMessage):
            request.append({"role": "user", "content": _user_content(message.content)})
        elif isinstance(message, AssistantMessage):
            request.append(_assistant_request_message(message, compat))
        else:
            text = "\n".join(
                b.text for b in message.content if isinstance(b, TextContent)
            )
            request.append(
                {"role": "tool", "tool_call_id": message.tool_call_id, "content": text}
            )
    return request


def _apply_thinking_dialect(
    params: dict, model: Model, options: StreamOptions, compat: CompatFlags
) -> None:
    """Emit the dialect's thinking controls into ``params`` in place."""
    level = normalize_thinking(options.thinking)
    if compat.thinking_format == "zai" and model.reasoning:
        params["thinking"] = (
            {"type": "enabled", "clear_thinking": False}
            if level is not None
            else {"type": "disabled"}
        )
        if compat.supports_reasoning_effort and level is not None:
            params["reasoning_effort"] = level
        return
    if compat.thinking_format == "deepseek" and model.reasoning:
        params["thinking"] = (
            {"type": "enabled"} if level is not None else {"type": "disabled"}
        )
        if compat.supports_reasoning_effort and level is not None:
            params["reasoning_effort"] = level
        return
    if (
        compat.thinking_format is None
        and compat.supports_reasoning_effort
        and level is not None
    ):
        # No thinking dialect: reasoning effort is the only control.
        params["reasoning_effort"] = level


def build_params(
    model: Model, tools: list[ToolDef], options: StreamOptions, compat: CompatFlags
) -> dict:
    """Assemble the chat-completions request body (without messages).

    The caller merges ``to_request_messages`` output into the returned
    dict under ``messages``. Output-token cap: ``options.max_tokens``
    overrides ``model.max_tokens`` (the catalog default) and is written
    under the dialect's ``max_tokens_field``. ``tool_choice`` is not set
    in v1.
    """
    params: dict = {"model": model.id, "stream": True}
    if compat.supports_usage_in_streaming:
        params["stream_options"] = {"include_usage": True}

    max_tokens = model.max_tokens if options.max_tokens is None else options.max_tokens
    if max_tokens:
        params[compat.max_tokens_field] = max_tokens
    if options.temperature is not None:
        params["temperature"] = options.temperature

    _apply_thinking_dialect(params, model, options, compat)

    if tools:
        params["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in tools
        ]
        if compat.zai_tool_stream:
            params["tool_stream"] = True
    return params


# ---------------------------------------------------------------------------
# Stream side: parsed SSE chunks → AssistantEvent
# ---------------------------------------------------------------------------

#: Reasoning output field candidates, in priority order (deepseek/llama.cpp
#: style ``reasoning_content`` first, then ``reasoning``, then
#: ``reasoning_text``). Per chunk the first non-empty string wins — some
#: servers echo the same text into two fields at once.
_REASONING_FIELDS = ("reasoning_content", "reasoning", "reasoning_text")

#: Known finish_reason values → unified StopReason; unknown values degrade
#: to "error" with a message naming the raw value (see _map_finish_reason).
_FINISH_REASON_MAP: dict[str, StopReason] = {
    "stop": "stop",
    "length": "length",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
}


@dataclass
class _ToolBlock:
    """One tool call being assembled from streamed argument fragments."""

    #: The wire ``index`` the fragments carry (None when routed by id).
    index: int | None
    call: ToolCall
    arguments_text: str = ""


def _reasoning_fragment(delta: dict) -> tuple[str, str | None]:
    """First non-empty reasoning fragment in a chunk delta, with its field.

    Returns ``("", None)`` when the delta carries no reasoning text.
    """
    for field in _REASONING_FIELDS:
        value = delta.get(field)
        if isinstance(value, str) and value:
            return value, field
    return "", None


def _wire_index(item: dict) -> int | None:
    """The item's tool ``index`` if present and a sane non-negative int."""
    value = item.get("index")
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _find_tool_block(blocks: list[_ToolBlock], item: dict) -> _ToolBlock | None:
    """Locate the block a tool-call fragment belongs to.

    Tracking is by chunk ``index`` first (OpenAI semantics); when the item
    carries no index, fall back to its ``id``.
    """
    index = _wire_index(item)
    if index is not None:
        return next((b for b in blocks if b.index == index), None)
    call_id = item.get("id")
    if isinstance(call_id, str) and call_id:
        return next((b for b in blocks if b.call.id == call_id), None)
    return None


def _index_of(content: list, block: object) -> int:
    """Position of ``block`` in ``partial.content``, by identity.

    Content blocks are dataclasses (value equality); two same-shaped blocks
    must never be confused, so lookup is identity-based.
    """
    for i, item in enumerate(content):
        if item is block:
            return i
    raise AssertionError("block is not in partial.content")


def _finalize_tool_arguments(block: _ToolBlock) -> None:
    """Parse the accumulated argument string into ``call.arguments``.

    Invalid JSON (or a non-object JSON value) degrades to ``arguments={}``
    plus ``arguments_error`` quoting up to 200 characters of the raw text;
    the stream itself completes normally. A call that streamed no argument
    text at all keeps the ``{}`` placeholder without an error.
    """
    call = block.call
    if not block.arguments_text:
        return
    try:
        arguments = json.loads(block.arguments_text)
    except json.JSONDecodeError:
        call.arguments = {}
        call.arguments_error = (
            f"invalid JSON arguments: {block.arguments_text[:200]}"
        )
        return
    if not isinstance(arguments, dict):
        call.arguments = {}
        call.arguments_error = (
            f"tool arguments are not a JSON object: {block.arguments_text[:200]}"
        )
        return
    call.arguments = arguments


def _map_finish_reason(raw: str | None) -> tuple[StopReason, str | None]:
    """Map the wire ``finish_reason`` onto a unified StopReason.

    Returns ``(stop_reason, error_message)``; a stream that never received
    a finish_reason is treated as "stop", an unrecognized value degrades to
    "error" with a message naming the raw value.
    """
    if raw is None:
        return "stop", None
    mapped = _FINISH_REASON_MAP.get(raw)
    if mapped is not None:
        return mapped, None
    return "error", f"unknown finish_reason: {raw!r}"


async def _events_from_chunks(
    model: Model,
    chunks: AsyncIterable[dict],
    abort: AbortToken | None = None,
) -> AsyncIterator[AssistantEvent]:
    """Assemble parsed chat-completion chunks into the event stream.

    This is the pure parsing core behind :func:`stream` and the offline
    fixture tests: ``chunks`` is an async iterable of already-parsed JSON
    chunk dicts, and ``abort`` is checked between chunks. Every event
    carries the same live ``partial`` AssistantMessage, mutated in place —
    consumers always see a ready snapshot. Block rules:

    * thinking precedes text in ``partial.content``; a reasoning delta that
      arrives after text started still appends to the (lazily created,
      re-positioned) thinking block;
    * an open thinking block ends when the first text delta or tool call
      starts (or at stream end); an open text block ends when the first
      tool call starts (or at stream end);
    * tool calls are tracked by wire ``index`` (fallback ``id``); argument
      fragments accumulate as a string and are parsed once at stream end.
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

    thinking: ThinkingContent | None = None
    thinking_open = False
    text: TextContent | None = None
    text_open = False
    tool_blocks: list[_ToolBlock] = []
    finish_reason: str | None = None

    async for chunk in chunks:
        if abort is not None and abort.aborted:
            partial.stop_reason = "aborted"
            yield AssistantEvent(type="error", partial=partial, reason="aborted")
            return
        if not isinstance(chunk, dict):
            continue
        if partial.response_id is None:
            response_id = chunk.get("id")
            if isinstance(response_id, str) and response_id:
                partial.response_id = response_id
        usage_raw = chunk.get("usage")
        if isinstance(usage_raw, dict):
            partial.usage = parse_usage(usage_raw, model.cost)
        choices = chunk.get("choices")
        if not isinstance(choices, list) or not choices:
            continue
        choice = choices[0]
        if not isinstance(choice, dict):
            continue
        raw_finish = choice.get("finish_reason")
        if isinstance(raw_finish, str) and raw_finish:
            finish_reason = raw_finish
        delta = choice.get("delta")
        if not isinstance(delta, dict):
            continue

        # Reasoning before content within each chunk: thinking precedes text.
        fragment, field = _reasoning_fragment(delta)
        if fragment:
            if thinking is None:
                thinking = ThinkingContent(thinking="", signature=field)
                # pi's rule: the thinking block must precede the text block
                # in content, so a late block is inserted before first text.
                for i, existing in enumerate(partial.content):
                    if isinstance(existing, TextContent):
                        partial.content.insert(i, thinking)
                        break
                else:
                    partial.content.append(thinking)
                thinking_open = True
                yield AssistantEvent(
                    type="thinking_start",
                    partial=partial,
                    content_index=_index_of(partial.content, thinking),
                )
            else:
                thinking.signature = field
            thinking.thinking += fragment
            yield AssistantEvent(
                type="thinking_delta",
                partial=partial,
                content_index=_index_of(partial.content, thinking),
                delta=fragment,
            )

        content_fragment = delta.get("content")
        if isinstance(content_fragment, str) and content_fragment:
            if thinking_open:  # text starts: the thinking block ends here
                yield AssistantEvent(
                    type="thinking_end",
                    partial=partial,
                    content_index=_index_of(partial.content, thinking),
                    content=thinking.thinking,
                )
                thinking_open = False
            if text is None:
                text = TextContent(text="")
                partial.content.append(text)
                text_open = True
                yield AssistantEvent(
                    type="text_start",
                    partial=partial,
                    content_index=_index_of(partial.content, text),
                )
            text.text += content_fragment
            yield AssistantEvent(
                type="text_delta",
                partial=partial,
                content_index=_index_of(partial.content, text),
                delta=content_fragment,
            )

        raw_calls = delta.get("tool_calls")
        if isinstance(raw_calls, list):
            for item in raw_calls:
                if not isinstance(item, dict):
                    continue
                function = item.get("function")
                function = function if isinstance(function, dict) else {}
                block = _find_tool_block(tool_blocks, item)
                if block is None:
                    if thinking_open:
                        yield AssistantEvent(
                            type="thinking_end",
                            partial=partial,
                            content_index=_index_of(partial.content, thinking),
                            content=thinking.thinking,
                        )
                        thinking_open = False
                    if text_open:
                        yield AssistantEvent(
                            type="text_end",
                            partial=partial,
                            content_index=_index_of(partial.content, text),
                            content=text.text,
                        )
                        text_open = False
                    call_id = item.get("id")
                    block = _ToolBlock(
                        index=_wire_index(item),
                        call=ToolCall(
                            id=(
                                call_id
                                if isinstance(call_id, str) and call_id
                                else f"call_{len(tool_blocks)}"
                            ),
                            name="",
                            arguments={},
                        ),
                    )
                    partial.content.append(block.call)
                    tool_blocks.append(block)
                    yield AssistantEvent(
                        type="toolcall_start",
                        partial=partial,
                        content_index=_index_of(partial.content, block.call),
                        tool_call=block.call,
                    )
                name = function.get("name")
                if isinstance(name, str) and name and not block.call.name:
                    block.call.name = name
                arguments_fragment = function.get("arguments")
                if isinstance(arguments_fragment, str) and arguments_fragment:
                    block.arguments_text += arguments_fragment
                    yield AssistantEvent(
                        type="toolcall_delta",
                        partial=partial,
                        content_index=_index_of(partial.content, block.call),
                        delta=arguments_fragment,
                        tool_call=block.call,
                    )

    # Chunks exhausted: close open blocks in content order (thinking before
    # text when both are somehow still open), assemble tool arguments once,
    # and emit the terminal event.
    for position, block in enumerate(partial.content):
        if block is thinking and thinking_open:
            yield AssistantEvent(
                type="thinking_end",
                partial=partial,
                content_index=position,
                content=block.thinking,
            )
            thinking_open = False
        elif block is text and text_open:
            yield AssistantEvent(
                type="text_end",
                partial=partial,
                content_index=position,
                content=block.text,
            )
            text_open = False
        elif isinstance(block, ToolCall):
            tool_block = next(t for t in tool_blocks if t.call is block)
            _finalize_tool_arguments(tool_block)
            yield AssistantEvent(
                type="toolcall_end",
                partial=partial,
                content_index=position,
                tool_call=block,
            )

    stop_reason, error_message = _map_finish_reason(finish_reason)
    partial.stop_reason = stop_reason
    if error_message is not None:
        partial.error_message = error_message
    yield AssistantEvent(
        type="done" if stop_reason != "error" else "error",
        partial=partial,
        reason=stop_reason,
    )


async def _chunk_stream(response: httpx.Response) -> AsyncIterator[dict]:
    """Yield parsed JSON chunks from a response's SSE data payloads.

    ``aiter_lines`` strips line terminators, so they are re-attached before
    the text goes through ``iter_sse_data`` (which consumes ``[DONE]``).
    Payloads that are not valid JSON are skipped silently — defensive
    against keep-alive noise some gateways inject into data frames.
    """

    async def _lines() -> AsyncIterator[str]:
        async for line in response.aiter_lines():
            yield f"{line}\n"

    async for payload in iter_sse_data(_lines()):
        try:
            yield json.loads(payload)
        except ValueError:  # json.JSONDecodeError subclasses ValueError
            continue


async def stream(
    model: Model,
    messages: list[Message],
    tools: list[ToolDef],
    options: StreamOptions,
) -> AsyncIterator[AssistantEvent]:
    """Stream one completion from an OpenAI-compatible endpoint.

    The single public entry: builds the request body (request side above),
    makes exactly one ``post_stream`` call, and drives
    :func:`_events_from_chunks` over the parsed SSE chunks. The ``partial``
    of the terminal ``done`` (reason "stop"/"length"/"tool_use") or
    ``error`` (reason "error"/"aborted") event is the final AssistantMessage
    with usage and stop_reason complete. A non-empty ``options.session_id``
    is sent as the gateway's ``x-opencode-session`` header.
    """
    params = build_params(model, tools, options, model.compat)
    params["messages"] = to_request_messages(messages, model.compat)

    headers = {
        "Authorization": f"Bearer {options.api_key}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    if options.extra_headers:
        headers.update(options.extra_headers)
    if options.session_id:
        headers["x-opencode-session"] = options.session_id
    url = f"{model.base_url.rstrip('/')}/chat/completions"

    client = default_client()
    try:
        response = await post_stream(client, url, headers, params, abort=options.abort)
        try:
            async for event in _events_from_chunks(
                model, _chunk_stream(response), options.abort
            ):
                yield event
        finally:
            await response.aclose()
    finally:
        await client.aclose()
