"""OpenAI-compatible chat-completions adapter.

Speaks the OpenAI ``/chat/completions`` wire dialect for every catalog
model whose ``api`` is ``openai-completions`` (plain OpenAI-compatible,
Z.AI GLM coding, DeepSeek, the OpenCode gateway …). Dialect quirks are
carried by :class:`~pyagent.providers.compat.CompatFlags`; this module
never looks at base_urls.

Two halves, both free of network code — the shared httpx/SSE transport
in ``transport.py`` does the I/O:

* Request side (below): ``to_request_messages`` maps the unified message
  model onto OpenAI message JSON, ``build_params`` assembles the request
  body from a catalog ``Model`` + ``StreamOptions`` under a dialect's
  compat flags (messages themselves are merged by the caller).
* Stream parsing (SSE deltas → ``AssistantEvent``) joins this module as
  it lands; keep new code below the request side.
"""

import json
from dataclasses import dataclass

from pyagent.events import AbortToken
from pyagent.providers.catalog import Model
from pyagent.providers.compat import CompatFlags
from pyagent.types import (
    AssistantMessage,
    Message,
    SystemMessage,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolDef,
    UserMessage,
)

__all__ = ["StreamOptions", "build_params", "to_request_messages"]


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
    if compat.thinking_format == "zai" and model.reasoning:
        params["thinking"] = (
            {"type": "enabled", "clear_thinking": False}
            if options.thinking is not None
            else {"type": "disabled"}
        )
        if compat.supports_reasoning_effort and options.thinking is not None:
            params["reasoning_effort"] = options.thinking
        return
    if compat.thinking_format == "deepseek" and model.reasoning:
        params["thinking"] = (
            {"type": "enabled"}
            if options.thinking is not None
            else {"type": "disabled"}
        )
        if compat.supports_reasoning_effort and options.thinking is not None:
            params["reasoning_effort"] = options.thinking
        return
    if (
        compat.thinking_format is None
        and compat.supports_reasoning_effort
        and options.thinking is not None
    ):
        # No thinking dialect: reasoning effort is the only control.
        params["reasoning_effort"] = options.thinking


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
