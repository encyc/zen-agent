"""Context composition report: measured total, estimated slices.

A provider returns one prompt-token total per request, never a per-segment
split. This module therefore keeps two kinds of numbers apart:

* **characters** are exact -- the prompt is assembled locally, and
  :class:`~zenagent.prompts.RenderedPrompt` records every block's size;
* **tokens** are estimated with the same heuristic request fitting uses
  (:mod:`zenagent.providers.limits`), and the UI shows them with ``≈``.

The measured total comes from the provider usage (``input + cache_read`` via
:func:`~zenagent.config.token_estimate`); slices are scaled or padded with an
"other" row so their percentages add up to the total the user actually sees.
"""

from __future__ import annotations

from dataclasses import dataclass

from zenagent.prompts import RenderedPrompt
from zenagent.providers.limits import (
    estimate_prompt_tokens,
    estimate_tool_tokens,
)
from zenagent.types import Message

__all__ = ["ContextReport", "ContextSlice", "build_context_report"]

#: (slice id, label, block-id prefix) in display order.
_GROUPS: tuple[tuple[str, str, str], ...] = (
    ("system", "system prompt", "system."),
    ("agents", "AGENTS.md", "context_files"),
    ("skills", "skills", "skills"),
    ("extensions", "extensions", "extensions"),
    ("plan", "plan note", "plan_mode"),
    ("mode", "mode note", "mode_note"),
)

_TAIL_GROUPS: tuple[tuple[str, str], ...] = (
    ("tools", "tool schemas"),
    ("session", "session"),
)


@dataclass(frozen=True)
class ContextSlice:
    """One component of the context (``chars`` exact, ``tokens`` estimated)."""

    id: str
    label: str
    chars: int
    tokens: int
    pct: float


@dataclass(frozen=True)
class ContextReport:
    """The context window split into slices, sized against the measured total."""

    slices: tuple[ContextSlice, ...]
    total_tokens: int
    window: int
    #: True when a provider usage supplied the total (not the estimator).
    measured: bool
    #: Sum of the per-slice estimates before scaling/residual handling.
    prompt_tokens: int


def _group_for(block_id: str) -> str | None:
    for group_id, _label, prefix in _GROUPS:
        if block_id == prefix or (
            prefix.endswith(".") and block_id.startswith(prefix)
        ):
            return group_id
    return None


def build_context_report(
    prompt_report: RenderedPrompt | None,
    tools: list | None,
    messages: list[Message],
    last_usage,
    context_used: int,
    window: int,
) -> ContextReport:
    """Split the current context into labelled slices (see module docstring).

    The total is the provider-measured prompt size when usage exists, otherwise
    the sum of the estimates: a resumed session carries cumulative usage totals,
    which are not a context size and must never become the denominator.
    """
    window = max(0, int(window or 0))
    measured = last_usage is not None

    chars: dict[str, int] = {}
    tokens: dict[str, int] = {}
    if prompt_report is not None:
        for block in prompt_report.blocks:
            group_id = _group_for(block.id)
            if group_id is None:
                continue
            chars[group_id] = chars.get(group_id, 0) + int(block.chars)
            tokens[group_id] = tokens.get(group_id, 0) + int(
                getattr(block, "tokens", 0) or 0
            )

    tool_tokens = estimate_tool_tokens(tools)
    if tool_tokens:
        tokens["tools"] = tool_tokens
    message_tokens = estimate_prompt_tokens(messages, None) if messages else 0
    if message_tokens:
        tokens["session"] = message_tokens

    entries: list[tuple[str, str, int, int]] = []
    for group_id, label, _prefix in _GROUPS:
        size = chars.get(group_id, 0)
        estimate = tokens.get(group_id, 0)
        if size or estimate:
            entries.append((group_id, label, size, estimate))
    for group_id, label in _TAIL_GROUPS:
        estimate = tokens.get(group_id, 0)
        if estimate:
            entries.append((group_id, label, 0, estimate))

    estimate_sum = sum(entry[3] for entry in entries)
    total = max(0, int(context_used or 0)) if measured else estimate_sum
    slices: list[ContextSlice] = []

    if total <= 0:
        slices = [
            ContextSlice(group_id, label, size, estimate, 0.0)
            for group_id, label, size, estimate in entries
        ]
    elif estimate_sum > total:
        scale = total / estimate_sum
        scaled = [
            [group_id, label, size, int(estimate * scale)]
            for group_id, label, size, estimate in entries
        ]
        used = sum(row[3] for row in scaled)
        if scaled and used < total:
            biggest = max(range(len(scaled)), key=lambda index: scaled[index][3])
            scaled[biggest][3] += total - used
        slices = [
            ContextSlice(group_id, label, size, estimate, estimate / total * 100.0)
            for group_id, label, size, estimate in scaled
        ]
    else:
        slices = [
            ContextSlice(
                group_id,
                label,
                size,
                estimate,
                estimate / total * 100.0,
            )
            for group_id, label, size, estimate in entries
        ]
        residual = total - estimate_sum
        if residual > 0:
            slices.append(
                ContextSlice(
                    "other",
                    "other (generation/overhead)",
                    0,
                    residual,
                    residual / total * 100.0,
                )
            )

    return ContextReport(
        slices=tuple(slices),
        total_tokens=total,
        window=window,
        measured=measured,
        prompt_tokens=estimate_sum,
    )
