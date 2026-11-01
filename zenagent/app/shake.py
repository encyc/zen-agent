"""``/shake``: shed heavy context at a chosen granularity.

omp's ``/shake`` drops one class of content without summarizing the rest:
tool results, images or thinking. The transcript is rewritten into a **new
session file** (the same recoverable convention as ``/undo`` and ``/compact``),
so the old file still holds everything.
"""

from __future__ import annotations

from dataclasses import replace

from zenagent.agent import Agent
from zenagent.agent.session import Session, unique_session_path
from zenagent.types import (
    AssistantMessage,
    ImageContent,
    TextContent,
    ThinkingContent,
    ToolResultMessage,
)

__all__ = ["KINDS", "shake"]

#: Accepted granularities; ``all`` is the union.
KINDS = ("tools", "images", "thinking")

_ELIDED_TOOL = "[tool result elided by /shake]"
_ELIDED_IMAGE = "[image removed by /shake]"


def _strip_images(message):
    content = getattr(message, "content", None)
    if not isinstance(content, list):
        return message, 0
    kept: list = []
    removed = 0
    for block in content:
        if isinstance(block, ImageContent):
            kept.append(TextContent(text=_ELIDED_IMAGE))
            removed += 1
        else:
            kept.append(block)
    if not removed:
        return message, 0
    return replace(message, content=kept), removed


def _strip_thinking(message):
    if not isinstance(message, AssistantMessage):
        return message, 0
    kept = [block for block in message.content if not isinstance(block, ThinkingContent)]
    removed = len(message.content) - len(kept)
    if not removed:
        return message, 0
    return replace(message, content=kept), removed


def shake(agent: Agent, kinds: set[str]) -> tuple[int, Session]:
    """Rewrite the transcript without the selected content classes.

    Returns ``(changed_messages, new_session)``. The agent is switched to the
    new transcript; the old session file is untouched.
    """
    if not agent.messages:
        raise ValueError("nothing to shake: the transcript is empty")
    changed = 0
    new_messages: list = []
    for message in agent.messages:
        if "tools" in kinds and isinstance(message, ToolResultMessage):
            message = replace(message, content=[TextContent(text=_ELIDED_TOOL)])
            changed += 1
        if "images" in kinds:
            message, removed = _strip_images(message)
            changed += removed
        if "thinking" in kinds:
            message, removed = _strip_thinking(message)
            changed += removed
        new_messages.append(message)
    session = Session(unique_session_path("shake"))
    for message in new_messages:
        session.append(message)
    agent.replace_messages(new_messages)
    return changed, session
