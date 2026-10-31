"""Request-level output-token fitting.

Chat-Completions-style providers reject a request whose prompt plus requested
output cap exceeds the model window: they do not clamp the cap for you. The
catalog default (``model.max_tokens``) can be a large share of the window, so a
long prompt would otherwise fail with a 400 before compaction ever triggers.

``fit_max_tokens`` keeps the requested cap when it fits and otherwise shrinks it
to the remaining room. ``fit_stream_options`` is the adapter-facing wrapper:
it only replaces ``options.max_tokens`` when the effective cap has to change,
so the common case keeps the catalog default (and providers that omit it).
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from typing import Any

from zenagent.providers.catalog import Model
from zenagent.types import (
    ImageContent,
    Message,
    TextContent,
    ThinkingContent,
    ToolCall,
)

__all__ = [
    "MIN_FITTED_OUTPUT_TOKENS",
    "OUTPUT_FIT_HEADROOM_TOKENS",
    "estimate_prompt_tokens",
    "estimate_tool_tokens",
    "fit_max_tokens",
    "fit_stream_options",
]

#: Never fit the cap below this while there is still room (a tiny cap turns a
#: turn into an immediate ``length`` stop); below it the fit wins to stay legal.
MIN_FITTED_OUTPUT_TOKENS = 1024

#: Per-request framing (generation prompt, role markers) no message count sees.
OUTPUT_FIT_HEADROOM_TOKENS = 64

_MESSAGE_FRAMING_TOKENS = 16

#: A decoded image is charged a flat estimate instead of its base64 length.
_IMAGE_TOKENS = 1_024


def _block_tokens(block: object) -> int:
    if isinstance(block, TextContent):
        return _estimate_text_tokens(block.text)
    if isinstance(block, ThinkingContent):
        return _estimate_text_tokens(block.thinking)
    if isinstance(block, ImageContent):
        return _IMAGE_TOKENS
    if isinstance(block, ToolCall):
        return _estimate_text_tokens(
            json.dumps(block.arguments, ensure_ascii=False, default=str)
        )
    return 0


def _message_tokens(message: Message) -> int:
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return _estimate_text_tokens(content)
    if isinstance(content, list):
        return sum(_block_tokens(block) for block in content)
    # Unknown block shapes: fall back to the serialized form.
    to_json = getattr(message, "to_json", None)
    if callable(to_json):
        try:
            return _estimate_text_tokens(
                json.dumps(to_json(), ensure_ascii=False, default=str)
            )
        except (TypeError, ValueError):  # pragma: no cover - defensive
            pass
    return max(1, len(str(content)) // 4)


def estimate_tool_tokens(tools: list[object] | None) -> int:
    """Approximate cost of the tool definitions sent with every request."""
    if not tools:
        return 0
    parts: list[str] = []
    for tool in tools:
        parts.append(str(getattr(tool, "name", "")))
        parts.append(str(getattr(tool, "description", "")))
        try:
            parts.append(json.dumps(getattr(tool, "parameters", {}), default=str))
        except (TypeError, ValueError):  # pragma: no cover - defensive
            parts.append(str(getattr(tool, "parameters", "")))
    return _estimate_text_tokens("\n".join(parts))


#: CJK/full-width ranges: these are ~1 token per character, not 4 chars/token.
_WIDE = re.compile(r"[\u2e80-\u9fff\uac00-\ud7af\uff00-\uffef\u3000-\u303f]")


def _estimate_text_tokens(text: str) -> int:
    # Start from the Latin heuristic, then add back the wide characters whose
    # real token cost is ~1/char (4 chars/token would underestimate badly).
    return len(text) // 4 + len(_WIDE.findall(text))


def estimate_prompt_tokens(
    messages: list[Message], tools: list[object] | None = None
) -> int:
    """Cheap upper-ish estimate: 4 latin chars/token, ~1 token per CJK char.

    Tool schemas are part of the request too, and a batch of MCP tools can be
    thousands of tokens, so they are counted when provided. Deliberately not a
    real tokenizer (the kernel has no tokenizer dependency); the margin is
    covered by ``OUTPUT_FIT_HEADROOM_TOKENS``.
    """
    total = estimate_tool_tokens(tools)
    for message in messages:
        total += max(1, _message_tokens(message))
        total += _MESSAGE_FRAMING_TOKENS
    return total


def fit_max_tokens(
    model: Model,
    messages: list[Message],
    requested: int | None,
    tools: list[object] | None = None,
) -> int | None:
    """Clamp ``requested`` so prompt + output stays inside the context window."""
    window = getattr(model, "context_window", 0) or 0
    if not requested or requested <= 0 or window <= 0:
        return requested
    room = (
        window - estimate_prompt_tokens(messages, tools) - OUTPUT_FIT_HEADROOM_TOKENS
    )
    if room >= requested:
        return requested
    if room < MIN_FITTED_OUTPUT_TOKENS:
        # The prompt nearly fills the window; the least-bad legal cap is what is
        # left (compaction/truncation handles the rest).
        return max(1, room)
    return room


def fit_stream_options(
    model: Model,
    messages: list[Message],
    options: Any,
    tools: list[object] | None = None,
) -> Any:
    """Return ``options`` (possibly replaced) with a context-fit output cap."""
    requested = (
        options.max_tokens if options.max_tokens is not None else model.max_tokens
    )
    fitted = fit_max_tokens(model, messages, requested, tools)
    if fitted is None or fitted == requested:
        return options
    if options.max_tokens is None and fitted == model.max_tokens:
        return options
    return replace(options, max_tokens=fitted)
