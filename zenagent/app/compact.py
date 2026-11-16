"""``/compact``: summarize the transcript in place, behind a replay boundary.

The session file stays append-only and the session itself keeps its identity:
the summary message is appended after a ``compaction`` boundary record, and
boundary-aware replay (:func:`zenagent.app.records.open_session`) returns only
the messages after the latest effective boundary. The pre-boundary history
stays in the file as the audit trail — bare ``Session`` reads and ``/export``
still see all of it. If summarization fails, nothing is appended.
"""

from __future__ import annotations

import time
from pathlib import Path

from zenagent.agent import Agent
from zenagent.agent.session import Session, session_title, set_session_title
from zenagent.app import records
from zenagent.prompts import compact_result, compose_compact
from zenagent.prompts.registry import read_block
from zenagent.providers.limits import estimate_text_tokens
from zenagent.providers.registry import StreamFn, safe_stream, stream_fn_for
from zenagent.providers.stream_options import StreamOptions
from zenagent.types import (
    AssistantMessage,
    Message,
    SystemMessage,
    TextContent,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
)

__all__ = [
    "AUTO_COMPACT_THRESHOLD",
    "KEEP_RECENT_TOKENS",
    "compact",
    "manual_keep_recent",
    "maybe_auto_compact",
    "should_auto_compact",
]

#: Fraction of the context window that triggers an automatic compaction.
AUTO_COMPACT_THRESHOLD = 0.85

#: Recent-context target for the keep-recent tail (pi learnings ③): the
#: newest messages within this estimated budget stay in context un-summarized.
KEEP_RECENT_TOKENS = 20_000


def _message_tokens(message: Message) -> int:
    """Estimated token cost of one message, via the shared heuristic."""
    return estimate_text_tokens(_render_transcript([message]))


def _orphans_tool_result(tail: list[Message]) -> bool:
    """True when a tail holds a tool result whose tool call is outside it."""
    call_ids = {
        block.id
        for message in tail
        if isinstance(message, AssistantMessage)
        for block in message.content
        if isinstance(block, ToolCall)
    }
    return any(
        isinstance(message, ToolResultMessage)
        and message.tool_call_id not in call_ids
        for message in tail
    )


def _choose_cut(
    messages: list[Message], keep_tokens: int, *, floor: int = 1
) -> int:
    """Index where the kept tail starts: ``floor <= cut <= len(messages)``.

    The tail (``messages[cut:]``) is the largest run of trailing messages
    whose estimated tokens fit ``keep_tokens`` — the head always keeps at
    least ``floor`` messages to summarize. A cut is never left between a
    tool call and its result: when the tail would hold a
    ``ToolResultMessage`` whose tool call is outside it, the cut walks back
    until the whole round is inside, even if that overshoots the budget.
    ``cut == len(messages)`` means nothing fits: no tail, the pre-tail
    full-summarize behavior.
    """
    if keep_tokens <= 0 or not messages:
        return len(messages)
    suffix = 0
    cut = len(messages)
    while cut > floor:
        cost = _message_tokens(messages[cut - 1])
        if suffix + cost > keep_tokens:
            break
        suffix += cost
        cut -= 1
    while cut > floor and _orphans_tool_result(messages[cut:]):
        cut -= 1
    if _orphans_tool_result(messages[cut:]):
        # The floor blocks walking back further: shrink the tail instead —
        # a tail holding an orphaned result would poison the next request.
        while cut < len(messages) and _orphans_tool_result(messages[cut:]):
            cut += 1
    return cut


def manual_keep_recent(messages: list[Message]) -> int:
    """Keep budget for an explicit ``/compact``: a quarter of the transcript.

    ``KEEP_RECENT_TOKENS`` is tuned for automatic compaction, which only fires
    near the context limit. Applying that budget to a manual compaction would
    keep the whole conversation verbatim and summarize almost nothing, so the
    manual path scales the tail down with the transcript — still leaving recent
    context unsummarized, but always freeing most of it.
    """
    total = sum(_message_tokens(message) for message in messages)
    return min(KEEP_RECENT_TOKENS, total // 4)


def _previous_summary(messages: list[Message]) -> str | None:
    """Raw summary text when the transcript leads with a compaction result.

    The wrapper text comes from the ``compact.result`` block itself, so the
    detection never drifts from what was written.
    """
    if not messages:
        return None
    first = messages[0]
    if not (isinstance(first, UserMessage) and isinstance(first.content, str)):
        return None
    prefix = read_block("compact.result").split("{{summary}}")[0]
    if first.content.startswith(prefix):
        return first.content[len(prefix) :].strip() or None
    return None


def _render_transcript(messages) -> str:
    """Flatten messages into role-tagged text for the summarizer."""
    lines: list[str] = []
    for message in messages:
        role = message.to_json().get("role", "unknown")
        if isinstance(message.content, str):  # SystemMessage / UserMessage text form
            text = message.content
        else:
            parts: list[str] = []
            for block in message.content:
                kind = block.to_json().get("type")
                if kind == "text":
                    parts.append(block.text)
                elif kind == "thinking":
                    parts.append(f"[thinking] {block.thinking}")
                elif kind == "toolCall":
                    parts.append(f"[tool_call {block.name}] {block.arguments}")
                elif kind == "image":
                    parts.append("[image]")
            text = "\n".join(parts)
        if role == "toolResult":
            continue  # tool output is summarized by the assistant's own text
        lines.append(f"## {role}\n{text}")
    return "\n\n".join(lines)


def _last_usage(agent: Agent) -> Usage | None:
    for message in reversed(agent.messages):
        if isinstance(message, AssistantMessage):
            return message.usage
    return None


def should_auto_compact(
    usage: Usage | None,
    context_window: int,
    threshold: float = AUTO_COMPACT_THRESHOLD,
) -> bool:
    """True when the last turn's prompt tokens crossed the compaction threshold."""
    if usage is None or context_window <= 0:
        return False
    used = usage.input + usage.cache_read
    return used >= context_window * threshold


async def maybe_auto_compact(
    agent: Agent,
    session: Session,
    *,
    threshold: float = AUTO_COMPACT_THRESHOLD,
    stream_fn: StreamFn | None = None,
    print_fn=print,
) -> Path | None:
    """Compact between runs when the context is nearly full.

    Never runs mid-run (nested model calls would complicate the transcript), and
    a failed summarization leaves the conversation untouched.
    """
    if agent.is_streaming or not agent.messages:
        return None
    if not should_auto_compact(_last_usage(agent), agent.model.context_window, threshold):
        return None
    try:
        path = await compact(agent, session, stream_fn, reason="auto")
    except Exception as exc:  # noqa: BLE001 - automatic path must never break the session
        print_fn(f"auto-compaction failed: {exc}")
        return None
    print_fn("context compacted")
    return path


async def compact(
    agent: Agent,
    session: Session,
    stream_fn: StreamFn | None = None,
    *,
    reason: str = "manual",
    tokens_before: int | None = None,
    keep_recent_tokens: int = KEEP_RECENT_TOKENS,
) -> Path:
    """Append the summary to ``session`` behind a compaction boundary.

    The cut follows the keep-recent discipline: the newest messages whose
    estimated tokens fit ``keep_recent_tokens`` stay in context un-summarized
    (the tail), and the cut never separates a tool call from its result. A
    re-compaction updates the previous summary instead of rebuilding it. The
    file keeps the full history — boundary replay returns summary + tail —
    and the session keeps its identity. Raises ``ValueError`` when there is
    nothing to compact and ``RuntimeError`` when the summarization call
    fails — in both cases nothing is written and the Agent is unchanged.
    """
    if not agent.messages:
        raise ValueError("nothing to compact: the transcript is empty")

    if tokens_before is None:
        usage = _last_usage(agent)
        tokens_before = usage.input + usage.cache_read if usage is not None else 0

    messages = list(agent.messages)
    previous = _previous_summary(messages)
    # With a previous summary in the lead, the head must still hold at least
    # one message that is not the summary itself — otherwise there is nothing
    # new to merge and the cut would strand the whole tail unsummarized.
    cut = _choose_cut(messages, keep_recent_tokens, floor=2 if previous else 1)
    head, tail = messages[:cut], messages[cut:]
    # A previous summary describes the head's lead message; what needs
    # merging is everything that came after it (the tail stays raw in
    # context either way — it is recent, not summarized).
    summarize_source = head[1:] if previous is not None else head
    if not summarize_source:
        raise ValueError(
            "nothing to compact: nothing new since the last summary"
        )
    transcript = _render_transcript(summarize_source)
    now = int(time.time())
    system_prompt, user_prompt = compose_compact(
        transcript, previous_summary=previous
    )
    request = [
        SystemMessage(content=system_prompt, timestamp=now),
        UserMessage(content=user_prompt, timestamp=now),
    ]
    options = StreamOptions(api_key=agent.api_key, thinking=None)
    fn = stream_fn or agent.stream_fn or stream_fn_for(agent.model)

    summary = ""
    error: str | None = None
    async for event in safe_stream(fn, agent.model, request, [], options):
        if event.type in ("done", "error"):
            summary = "".join(
                block.text
                for block in event.partial.content
                if isinstance(block, TextContent)
            )
            error = event.partial.error_message
    if error:
        raise RuntimeError(f"compaction failed: {error}")
    if not summary.strip():
        raise RuntimeError("compaction produced an empty summary")

    # Pin the display title first: after the cut the first message is the
    # summary, and a derived title would collapse to its header line.
    set_session_title(session.path, session_title(session.path))
    session.append_meta(
        records.compaction(tokens_before, reason=reason, kept=len(tail))
    )
    message = UserMessage(
        content=compact_result(summary.strip()), timestamp=int(time.time())
    )
    await session.append(message)
    # The tail lands after the boundary too, so boundary-aware replay returns
    # summary + tail — exactly the live view the Agent now holds. The file
    # stays append-only: the pre-boundary copies remain as the audit trail.
    for kept_message in tail:
        await session.append(kept_message)
    agent.replace_messages([message, *tail])
    session.load()  # re-sync the cached view with the replay cut
    return session.path
