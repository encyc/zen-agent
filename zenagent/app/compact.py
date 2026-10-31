"""``/compact``: summarize the transcript into a fresh session file.

The old JSONL stays on disk untouched (it is the audit trail); the compacted
conversation is written to a *new* session file and becomes the Agent's
transcript. If summarization fails, nothing changes.
"""

from __future__ import annotations

import time
from pathlib import Path

from zenagent.agent import Agent
from zenagent.agent.session import Session, unique_session_path
from zenagent.prompts import compact_result, compose_compact
from zenagent.providers.openai_completions import StreamOptions
from zenagent.providers.registry import StreamFn, safe_stream, stream_fn_for
from zenagent.types import (
    AssistantMessage,
    SystemMessage,
    TextContent,
    Usage,
    UserMessage,
)

__all__ = [
    "AUTO_COMPACT_THRESHOLD",
    "compact",
    "maybe_auto_compact",
    "should_auto_compact",
]

#: Fraction of the context window that triggers an automatic compaction.
AUTO_COMPACT_THRESHOLD = 0.85


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
        path = await compact(agent, stream_fn)
    except Exception as exc:  # noqa: BLE001 - automatic path must never break the session
        print_fn(f"auto-compaction failed: {exc}")
        return None
    print_fn(f"auto-compacted into {path.name}")
    return path


async def compact(agent: Agent, stream_fn: StreamFn | None = None) -> Path:
    """Replace the transcript with a summary; returns the new session path.

    Raises ``ValueError`` when there is nothing to compact and ``RuntimeError``
    when the summarization call fails — in both cases the Agent is unchanged.
    """
    if not agent.messages:
        raise ValueError("nothing to compact: the transcript is empty")

    transcript = _render_transcript(agent.messages)
    now = int(time.time())
    system_prompt, user_prompt = compose_compact(transcript)
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

    message = UserMessage(
        content=compact_result(summary.strip()), timestamp=int(time.time())
    )
    session = Session(unique_session_path("compact"))
    session.append(message)
    agent.replace_messages([message])
    return session.path
