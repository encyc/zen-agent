"""Typed transcript rows.

Each row owns one visual role from omp's transcript vocabulary: a filled user
bar, a one-line thinking row that opens on demand, a dim usage tail. Keeping
them as widgets (rather than formatting strings) is what lets the transcript
style them independently and keeps the container dumb.
"""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

from zenagent.app.views import MessageView, RunStatus

__all__ = ["NoticeRow", "ThinkingRow", "UsageRow", "UserBlock"]

_TEXT = "#d7e0e8"
_MUTED = "#8296a6"
_DIM = "#5d6f7e"
_ACCENT = "#d9a86c"
_ERROR = "#e08787"


class UserBlock(Static):
    """The user's turn as a filled, full-width bar -- no prefix, no quoting."""

    def __init__(self, text: str) -> None:
        self._text = text
        super().__init__(Text(text, style=_TEXT), classes="user-message")

    @property
    def plain(self) -> str:
        return self._text


class ThinkingRow(Static):
    """One muted line while collapsed; the full reasoning when opened."""

    can_focus = True
    BINDINGS = [("enter,space", "toggle", "Expand")]  # noqa: RUF012 - Textual class API

    def __init__(self, thinking: str) -> None:
        self.thinking = thinking
        self.collapsed = True
        super().__init__(self._renderable(), classes="thinking-row")

    @property
    def plain(self) -> str:
        return self._renderable().plain

    def _renderable(self) -> Text:
        text = Text()
        if self.collapsed:
            chars = len(self.thinking)
            label = f"{chars / 1000:.1f}k" if chars >= 1000 else str(chars)
            text.append("✻ ", style=_DIM)
            text.append(f"thinking · {label} chars", style=f"italic {_MUTED}")
            return text
        text.append("✻ ", style=_DIM)
        text.append("thinking\n", style=_MUTED)
        for line in self.thinking.splitlines():
            text.append("  ")
            text.append(line, style=f"italic {_MUTED}")
            text.append("\n")
        return text

    def set_expanded(self, expanded: bool) -> None:
        if expanded == (not self.collapsed):
            return
        self.collapsed = not expanded
        if self.is_mounted:
            self.update(self._renderable())

    def set_thinking(self, thinking: str) -> None:
        """Update the reasoning text (streaming keeps growing it)."""
        if thinking == self.thinking:
            return
        self.thinking = thinking
        self.update(self._renderable())

    def action_toggle(self) -> None:
        self.collapsed = not self.collapsed
        self.update(self._renderable())


class UsageRow(Static):
    """The dim tail of a finished turn."""

    def __init__(self, status: RunStatus) -> None:
        super().__init__(_usage_text(status), classes="usage-row")

    @property
    def plain(self) -> str:
        return self.renderable.plain if isinstance(self.renderable, Text) else str(self.renderable)


def _usage_text(status: RunStatus) -> Text:
    parts = [f"in {status.input_tokens}", f"out {status.output_tokens}"]
    if status.cache_read:
        parts.append(f"cache {status.cache_read}")
    parts.append(f"${status.cost:.4f}")
    return Text("done · " + " · ".join(parts), style=_DIM)


class DividerRow(Static):
    """A dim one-line divider: context was compacted at this point."""

    def __init__(self, text: str) -> None:
        self._text = f"── {text} ──"
        super().__init__(Text(self._text, style=_DIM), classes="divider-row")

    @property
    def plain(self) -> str:
        return self._text


class NoticeRow(Static):
    """A quiet one-off line: command output, unknown command, warnings."""

    def __init__(self, text: str, *, error: bool = False) -> None:
        self._text = text
        super().__init__(Text(text, style=_ERROR if error else _MUTED), classes="notice")

    @property
    def plain(self) -> str:
        return self._text


def assistant_text(view: MessageView) -> Text:
    """Plain-text fallback for an assistant turn with no markdown renderer."""
    return Text(view.text, style=_TEXT)
