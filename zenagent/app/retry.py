"""Shared retry/rewind: drop the last turn and re-send its prompt.

Both frontends need the same move: find the last user message, truncate the
transcript *before* it, write the truncated transcript to a new (recoverable)
session file, and hand the prompt text back so the caller can run it again.
"""

from __future__ import annotations

from zenagent.agent import Agent
from zenagent.agent.session import Session, truncate_last_turn
from zenagent.types import ImageContent, TextContent, UserMessage

__all__ = ["prepare_retry", "user_text"]


def user_text(message: UserMessage) -> str:
    """Flatten a user message (text and image markers) back into prompt text."""
    content = message.content
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content:
        if isinstance(block, TextContent):
            parts.append(block.text)
        elif isinstance(block, ImageContent):
            parts.append(f"[image {block.mime_type}]")
    return "\n".join(parts)


def prepare_retry(agent: Agent, session: Session) -> tuple[str, Session] | None:
    """Prepare one retry; ``None`` when there is no answered turn to replay.

    Mutates ``agent`` (the transcript is truncated) and returns the new session
    file; the caller owns re-sending the prompt. Returns ``None`` while a run is
    streaming so retry never races the loop.
    """
    if agent.is_streaming:
        return None
    messages = list(agent.messages)
    last_user = next(
        (
            position
            for position in range(len(messages) - 1, -1, -1)
            if isinstance(messages[position], UserMessage)
        ),
        None,
    )
    if last_user is None or last_user == len(messages) - 1:
        return None  # nothing answered yet
    text = user_text(messages[last_user])
    if not text.strip():
        return None

    truncated = truncate_last_turn(messages)
    agent.replace_messages(truncated)
    new_path = Session(session.path).rewrite(truncated)
    return text, Session(new_path)
