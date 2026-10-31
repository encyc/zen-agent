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
from dataclasses import replace
from typing import Any

from zenagent.providers.catalog import Model
from zenagent.types import Message

__all__ = [
    "MIN_FITTED_OUTPUT_TOKENS",
    "OUTPUT_FIT_HEADROOM_TOKENS",
    "estimate_prompt_tokens",
    "fit_max_tokens",
    "fit_stream_options",
]

#: Never fit the cap below this while there is still room (a tiny cap turns a
#: turn into an immediate ``length`` stop); below it the fit wins to stay legal.
MIN_FITTED_OUTPUT_TOKENS = 1024

#: Per-request framing (generation prompt, role markers) no message count sees.
OUTPUT_FIT_HEADROOM_TOKENS = 64

_MESSAGE_FRAMING_TOKENS = 16


def _message_text(message: Message) -> str:
    to_json = getattr(message, "to_json", None)
    if callable(to_json):
        try:
            return json.dumps(to_json(), ensure_ascii=False)
        except (TypeError, ValueError):  # pragma: no cover - defensive
            pass
    return str(getattr(message, "content", ""))


def estimate_prompt_tokens(messages: list[Message]) -> int:
    """Cheap upper-ish estimate: 4 chars/token plus per-message framing.

    Deliberately not a real tokenizer (the kernel has no tokenizer dependency);
    the margin is covered by ``OUTPUT_FIT_HEADROOM_TOKENS``.
    """
    total = 0
    for message in messages:
        total += max(1, len(_message_text(message)) // 4) + _MESSAGE_FRAMING_TOKENS
    return total


def fit_max_tokens(
    model: Model, messages: list[Message], requested: int | None
) -> int | None:
    """Clamp ``requested`` so prompt + output stays inside the context window."""
    window = getattr(model, "context_window", 0) or 0
    if not requested or requested <= 0 or window <= 0:
        return requested
    room = window - estimate_prompt_tokens(messages) - OUTPUT_FIT_HEADROOM_TOKENS
    if room >= requested:
        return requested
    if room < MIN_FITTED_OUTPUT_TOKENS:
        # The prompt nearly fills the window; the least-bad legal cap is what is
        # left (compaction/truncation handles the rest).
        return max(1, room)
    return room


def fit_stream_options(model: Model, messages: list[Message], options: Any) -> Any:
    """Return ``options`` (possibly replaced) with a context-fit output cap."""
    requested = (
        options.max_tokens if options.max_tokens is not None else model.max_tokens
    )
    fitted = fit_max_tokens(model, messages, requested)
    if fitted is None or fitted == requested:
        return options
    if options.max_tokens is None and fitted == model.max_tokens:
        return options
    return replace(options, max_tokens=fitted)
