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
import time
from collections.abc import Callable
from pathlib import Path

from zenagent.agent.session import Session
from zenagent.types import (
    AssistantMessage,
    Message,
    TextContent,
    ToolCall,
    ToolResultMessage,
)

__all__ = [
    "BRANCH",
    "COMPACTION",
    "GOAL",
    "KINDS",
    "MODE",
    "RETRY",
    "RUN_END",
    "RUN_START",
    "SYNTHESIZED_RESULT_TEXT",
    "TOOL_DETAILS_LIMIT",
    "TOOL_DIFF_MAX_LINES",
    "TOOL_END",
    "branch",
    "branch_window",
    "compaction",
    "drop_orphan_tool_results",
    "goal",
    "is_branch",
    "is_compaction",
    "is_goal",
    "is_tool_end",
    "keep_for_rewrite",
    "mode",
    "open_session",
    "record_type",
    "repair_interrupted_run",
    "retry",
    "run_end",
    "run_start",
    "tool_end",
]

#: A completed tool call: duration, error flag and bounded UI details.
TOOL_END = "tool_end"
#: A context compaction: how many tokens it replaced and where it came from.
COMPACTION = "compaction"
#: One full goal snapshot; the last one in the file wins.
GOAL = "goal"
#: A run began (the boundary the transcript itself cannot express).
RUN_START = "run_start"
#: A run ended: termination reason, turns, wall clock and the last error.
RUN_END = "run_end"
#: One swallowed transient provider failure.
RETRY = "retry"
#: The permission mode the session switched to.
MODE = "mode"
#: A durable rewind branch: the live replay rescope and how much was abandoned.
BRANCH = "branch"

#: Record kinds this version defines. Anything else is carried verbatim by a
#: transcript rewrite (see ``keep_for_rewrite``).
KNOWN_KINDS = frozenset(
    {TOOL_END, COMPACTION, GOAL, RUN_START, RUN_END, RETRY, MODE, BRANCH}
)

#: Every kind this build writes. Unknown kinds are read back untouched but are
#: never carried into a rewritten transcript.
KINDS = (TOOL_END, COMPACTION, GOAL, RUN_START, RUN_END, RETRY, MODE, BRANCH)

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


def is_goal(record: dict) -> bool:
    return record_type(record) == GOAL


def branch(cut: int, skip: int, *, call: str = "", goal: str = "") -> dict:
    """One ``branch`` record: the live replay keeps ``messages[:cut]`` plus
    everything appended after this record; ``skip`` messages were abandoned
    and stay in the file as the audit trail. ``call`` names the rewind tool
    call whose own result must leave the projection with the abandoned span
    (in-memory and replayed views must never drift)."""
    record: dict = {"type": BRANCH, "cut": int(cut), "skip": int(skip)}
    if call:
        record["call"] = call
    if goal:
        record["goal"] = goal
    return record


def is_branch(record: dict) -> bool:
    return record_type(record) == BRANCH


def drop_orphan_tool_results(messages: list[Message]) -> list[Message]:
    """Drop tool results whose tool call is no longer in ``messages``.

    A replay projection (branch window, rewind) can cut a tool call out of the
    transcript while its result survives — a provider rejects a result whose
    call is missing, so every projection applies this same rule and the live
    and replayed views stay equal.
    """
    call_ids = {
        block.id
        for message in messages
        if isinstance(message, AssistantMessage)
        for block in message.content
        if isinstance(block, ToolCall)
    }
    kept = [
        message
        for message in messages
        if not (
            isinstance(message, ToolResultMessage)
            and message.tool_call_id not in call_ids
        )
    ]
    return kept if len(kept) != len(messages) else messages


def branch_window(record: dict, messages: list) -> list | None:
    """The structural selector behind ``Session(window=...)`` for branches.

    Returns the projected replay — head before the cut, abandoned span
    dropped, orphaned tool results removed — or ``None`` when the record is
    not a branch or its positions no longer fit (a torn or corrupt record must
    never lose the transcript).
    """
    if record_type(record) != BRANCH:
        return None
    cut = record.get("cut")
    skip = record.get("skip")
    if not isinstance(cut, int) or not isinstance(skip, int):
        return None
    if cut < 0 or skip < 1 or cut + skip > len(messages):
        return None
    return drop_orphan_tool_results([*messages[:cut], *messages[cut + skip :]])


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


def goal(state: dict | None) -> dict:
    """One complete goal snapshot; ``None`` clears the session's goal."""
    return {"type": GOAL, "goal": state}


def compaction(tokens_before: int, reason: str, *, kept: int = 0) -> dict:
    """One ``compaction`` record: the replay boundary and its divider.

    ``kept`` counts the recent-tail messages that stayed in context after the
    cut (0 = the pre-tail full-summarize behavior).
    """
    return {
        "type": COMPACTION,
        "tokensBefore": int(tokens_before),
        "reason": reason,
        "kept": int(kept),
    }


def open_session(
    path: Path,
    *,
    lazy: bool = False,
    cwd: Path | str | None = None,
) -> Session:
    """A session whose replay cuts at compaction boundaries (model context).

    Every context consumer opens sessions through this — resume, switch, undo,
    retry seed the Agent from the compacted segment only — while listings,
    titles and export keep the whole file (bare ``Session``). The display reads
    the full view and positions the divider from ``Session.segment_start``.
    Branch records rescope the replay the same way: a durable rewind's
    abandoned span stays in the file but out of the live context.

    ``lazy`` is for sessions the runtime itself just created: their header and
    starting records stay in memory until the first message (see
    :meth:`Session.materialize`), so an idle launch writes no transcript.
    """
    return Session(
        path, boundary=is_compaction, window=branch_window, lazy=lazy, cwd=cwd
    )


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


def keep_for_rewrite(
    *, keep_tool_end: bool = True, keep_goal: bool = True
) -> Callable[[dict], bool]:
    """Predicate for :meth:`Session.carry_records` after a transcript rewrite.

    ``compaction`` records always travel (the divider still happened);
    ``goal`` records travel with undo/retry/compact, but a fork can drop them
    so a new branch does not inherit an active loop; ``tool_end`` records travel
    so cards keep their durations and diffs, unless the caller is about to drop
    the tool calls themselves. Run boundaries, retries, mode switches and
    unknown kinds are dropped: a truncated file must not claim events whose
    messages are gone.
    """

    def keep(record: dict) -> bool:
        kind = record_type(record)
        if kind == COMPACTION:
            return True
        if kind == GOAL:
            return keep_goal
        if keep_tool_end and kind == TOOL_END:
            return True
        # An unknown kind (a newer file, or a plugin's side-channel) is inert
        # on replay, so carry it verbatim instead of silently deleting another
        # version's data (audit P2).
        return kind not in KNOWN_KINDS

    return keep


#: Model-facing text of a synthesized result for a call the last assistant
#: turn issued but no result ever answered (an interrupted run).
SYNTHESIZED_RESULT_TEXT = (
    "This tool call never completed: the session was interrupted before its "
    "result was recorded. Its outcome is unknown — inspect the current state "
    "before re-issuing the call."
)


async def repair_interrupted_run(
    session: Session, messages: list[Message]
) -> list[Message]:
    """Synthesize unknown-outcome results for dangling tool calls on resume.

    A crash between the assistant's ``message_end`` and its tool results (a
    torn tail, a kill mid tool-run) leaves the transcript's last assistant
    turn holding calls that nothing answers. ``replayable`` would drop them
    from the next request silently; this appends one error result per
    unanswered call instead, so the file, the display and the model all learn
    the outcome is unknown. Only the *last* assistant turn is scanned: the
    append-only flow answers earlier turns before the next one starts, and a
    torn tail can only cut the end.

    Calls of failed/aborted turns keep their ``replayable`` treatment — the
    synthesized results only ever inform the model about healthy ``tool_use``
    turns cut by a crash. No-op (nothing written) when there is no file, no
    assistant message, no calls, or every call is answered.
    """
    if not messages or not session.path.is_file():
        return messages
    last_at = next(
        (
            index
            for index in range(len(messages) - 1, -1, -1)
            if isinstance(messages[index], AssistantMessage)
        ),
        None,
    )
    if last_at is None:
        return messages
    answered = {
        message.tool_call_id
        for message in messages[last_at + 1 :]
        if isinstance(message, ToolResultMessage)
    }
    dangling = [
        block
        for block in messages[last_at].content
        if isinstance(block, ToolCall)
        and not (block.id == "" and block.name == "")
        and block.id not in answered
    ]
    if not dangling:
        return messages
    now = int(time.time())
    for call in dangling:  # source order: the transcript must read coherently
        result = ToolResultMessage(
            tool_call_id=call.id,
            tool_name=call.name,
            content=[TextContent(text=SYNTHESIZED_RESULT_TEXT)],
            is_error=True,
            timestamp=now,
        )
        await session.append(result)
        messages.append(result)
    return messages
