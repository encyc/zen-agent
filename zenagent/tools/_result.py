"""Shared helpers for the tool author: one result shape, one truncation notice.

Every builtin tool builds a ``ToolResultAgent`` the same way and clips its body
with the same marker (``"\\n… (truncated)"``). Keeping both here means a house
style change is one edit, not nine.
"""

from __future__ import annotations

from zenagent.agent.loop import ToolResultAgent
from zenagent.types import TextContent

__all__ = ["clip", "text_result"]

#: Appended (exactly once) when a tool body exceeds its own cap.
TRUNCATION_NOTICE = "\n… (truncated)"


def text_result(text: str, *, is_error: bool = False, **details) -> ToolResultAgent:
    """One tool result: ``text`` is all the model sees, ``details`` is UI-only."""
    return ToolResultAgent(
        content=[TextContent(text=text)], details=details, is_error=is_error
    )


def clip(text: str, limit: int) -> tuple[str, bool]:
    """Cut ``text`` to ``limit`` characters, saying so the same way everywhere."""
    if len(text) <= limit:
        return text, False
    return text[:limit] + TRUNCATION_NOTICE, True
