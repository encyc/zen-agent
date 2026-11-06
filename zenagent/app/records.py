"""The session side-channel record vocabulary.

One session file tells the whole story: normal messages are transcript lines,
and interleaved flat records (``{"type": ..., "timestamp": ..., ...}``) carry
the facts the messages cannot — where the run started and why it ended, which
transient failures were retried, when the permission mode changed, what each
tool call cost and returned, when the context was compacted.

This module is the single owner of that vocabulary: the type constants, one
builder per kind, the read predicates, and the predicate that decides which
records still describe a transcript after a rewrite. The kernel
(:mod:`zenagent.agent.session`) only knows the *structure* — a record that
carries a ``call_id`` follows the calls that still exist — never a record kind.

Everything here is best-effort on the write path: :meth:`Session.append_meta`
swallows ``OSError``, so observability can never abort a run.
"""

from __future__ import annotations

import json
from collections.abc import Callable

__all__ = [
    "COMPACTION",
    "KINDS",
    "MODE",
    "RETRY",
    "RUN_END",
    "RUN_START",
    "TOOL_DETAILS_LIMIT",
    "TOOL_DIFF_MAX_LINES",
    "TOOL_END",
    "compaction",
    "is_compaction",
    "is_tool_end",
    "keep_for_rewrite",
    "mode",
    "record_type",
    "retry",
    "run_end",
    "run_start",
    "tool_end",
]

#: A completed tool call: duration, error flag and bounded UI details.
TOOL_END = "tool_end"
#: A context compaction: how many tokens it replaced and where it came from.
COMPACTION = "compaction"
#: A run began (the boundary the transcript itself cannot express).
RUN_START = "run_start"
#: A run ended: termination reason, turns, wall clock and the last error.
RUN_END = "run_end"
#: One swallowed transient provider failure.
RETRY = "retry"
#: The permission mode the session switched to.
MODE = "mode"

#: Every kind this build writes. Unknown kinds are read back untouched but are
#: never carried into a rewritten transcript.
KINDS = (TOOL_END, COMPACTION, RUN_START, RUN_END, RETRY, MODE)

#: Serialized cap for one ``tool_end`` record's ``details`` (UI-only payload).
TOOL_DETAILS_LIMIT = 16_384

#: Line cap for a stored diff before it is written to the session file.
TOOL_DIFF_MAX_LINES = 400


def record_type(record: dict) -> str | None:
    """The record's ``type``, or ``None`` when the line is not a record."""
    kind = record.get("type")
    return kind if isinstance(kind, str) else None


def is_tool_end(record: dict) -> bool:
    return record_type(record) == TOOL_END


def is_compaction(record: dict) -> bool:
    return record_type(record) == COMPACTION


def _json_size(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False, default=str))


def _bound_details(details: dict | None) -> dict:
    """Bound a tool's UI details: diff line cap, then a serialized size cap."""
    data = dict(details or {})
    diff = data.get("diff")
    if isinstance(diff, str):
        lines = diff.splitlines()
        if len(lines) > TOOL_DIFF_MAX_LINES:
            data["diff"] = "\n".join(lines[:TOOL_DIFF_MAX_LINES])
            data["diff_truncated"] = True
    while _json_size(data) > TOOL_DETAILS_LIMIT and data:
        if "diff" in data:
            data.pop("diff")
            data["diff_truncated"] = True
            continue
        key = max(
            (
                name
                for name in data
                if name not in ("diff_truncated", "details_truncated")
            ),
            key=lambda name: _json_size(data[name]),
            default=None,
        )
        if key is None:
            break
        data.pop(key)
        data["details_truncated"] = True
    return data


def tool_end(
    call_id: str,
    tool_name: str,
    duration_ms: int | None,
    is_error: bool,
    details: dict | None,
) -> dict:
    """One ``tool_end`` record; ``append_meta`` stamps the timestamp."""
    record: dict = {
        "type": TOOL_END,
        "call_id": call_id,
        "tool_name": tool_name,
        "is_error": bool(is_error),
    }
    if duration_ms is not None:
        record["duration_ms"] = int(duration_ms)
    bounded = _bound_details(details)
    if bounded:
        record["details"] = bounded
    return record


def compaction(tokens_before: int, derived_from: str, reason: str) -> dict:
    """One ``compaction`` record: the divider a resumed session redraws."""
    return {
        "type": COMPACTION,
        "tokensBefore": int(tokens_before),
        "derivedFrom": derived_from,
        "reason": reason,
    }


def run_start() -> dict:
    return {"type": RUN_START}


def run_end(
    *,
    termination: str | None,
    turns: int | None,
    duration_ms: int | None = None,
    detail: str | None = None,
) -> dict:
    record: dict = {"type": RUN_END, "termination": termination, "turns": turns}
    if duration_ms is not None:
        record["duration_ms"] = int(duration_ms)
    if detail:
        record["detail"] = detail
    return record


def retry(
    *, attempt: int, max_retries: int, delay_ms: int, error: str, model: str
) -> dict:
    return {
        "type": RETRY,
        "attempt": int(attempt),
        "max_retries": int(max_retries),
        "delay_ms": int(delay_ms),
        "error": error,
        "model": model,
    }


def mode(value: str) -> dict:
    """One ``mode`` record: the permission mode a session runs under."""
    return {"type": MODE, "mode": value}


def keep_for_rewrite(*, keep_tool_end: bool = True) -> Callable[[dict], bool]:
    """Predicate for :meth:`Session.carry_records` after a transcript rewrite.

    ``compaction`` records always travel (the divider still happened);
    ``tool_end`` records travel so cards keep their durations and diffs, unless
    the caller is about to drop the tool calls themselves. Run boundaries,
    retries, mode switches and unknown kinds are dropped: a truncated file must
    not claim events whose messages are gone.
    """

    def keep(record: dict) -> bool:
        kind = record_type(record)
        if kind == COMPACTION:
            return True
        return keep_tool_end and kind == TOOL_END

    return keep
