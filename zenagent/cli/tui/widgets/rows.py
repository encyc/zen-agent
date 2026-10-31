"""Typed transcript rows.

Each row owns one visual role from omp's transcript vocabulary: a filled user
bar, a one-line thinking row that opens on demand, a dim usage tail. Keeping
them as widgets (rather than formatting strings) is what lets the transcript
style them independently and keeps the container dumb.
"""

from __future__ import annotations

import time

from rich.text import Text
from textual.timer import Timer
from textual.widgets import Static

from zenagent.app.views import MessageView, RunStatus

__all__ = [
    "NoticeRow",
    "ThinkingRow",
    "UsageRow",
    "UserBlock",
    "format_thinking_duration",
]

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


def _preview_line(thinking: str) -> str:
    """The first line with content: the collapsed row's one-line summary."""
    for line in thinking.splitlines():
        if line.strip():
            return line.strip()
    return ""


def format_thinking_duration(seconds: float) -> str:
    """``3.2s`` under ten seconds, whole seconds above, ``m ss`` past a minute."""
    if seconds < 10:
        return f"{max(0.0, seconds):.1f}s"
    total = round(seconds)
    if total < 60:
        return f"{total}s"
    minutes, rest = divmod(total, 60)
    return f"{minutes}m {rest:02d}s"


class ThinkingRow(Static):
    """A one-line preview while collapsed; the full reasoning when opened.

    The head mirrors the tool cards: a glyph, a label, the first line of the
    body dimmed and clipped to the row, and the elapsed time. While the model is
    still reasoning the label ends in ``…`` and the duration ticks (the glyph is
    accented); once the block ends the duration freezes at the total.
    """

    can_focus = True
    BINDINGS = [("enter,space", "toggle", "Expand")]  # noqa: RUF012 - Textual class API

    #: Seconds between live-duration repaints while the model is reasoning.
    TICK = 0.5

    def __init__(
        self,
        thinking: str,
        *,
        started_at: float | None = None,
        seconds: float | None = None,
    ) -> None:
        self.thinking = thinking
        self.collapsed = True
        self.started_at = started_at
        self.seconds = seconds
        self._ticker: Timer | None = None
        super().__init__(self._renderable(), classes="thinking-row")

    @property
    def plain(self) -> str:
        return self._renderable().plain

    @property
    def active(self) -> bool:
        """True while the model is still reasoning (the duration ticks)."""
        return self.started_at is not None

    @property
    def duration_text(self) -> str | None:
        """``3.2s`` while reasoning, the frozen total after, None on replay."""
        if self.started_at is not None:
            elapsed = (self.seconds or 0.0) + (time.monotonic() - self.started_at)
            return format_thinking_duration(elapsed)
        if self.seconds is not None:
            return format_thinking_duration(self.seconds)
        return None

    def on_mount(self) -> None:
        # The constructor rendered before the widget had a width: redo it now.
        self.update(self._renderable())
        self._sync_ticker()

    def on_unmount(self) -> None:
        self._stop_ticker()

    def on_resize(self) -> None:
        # The preview is clipped to the row, so a narrower terminal re-clips it.
        self.update(self._renderable())

    def _width(self) -> int:
        try:
            width = self.size.width
        except RuntimeError:  # not mounted yet: render against a default width
            width = 0
        return max(24, (width or 80) - 2)

    def _sync_ticker(self) -> None:
        # Called from on_mount (where Textual has not flipped is_mounted yet)
        # and from streaming updates; set_interval is safe in both places.
        if self.active:
            if self._ticker is None:
                self._ticker = self.set_interval(self.TICK, self._tick)
        else:
            self._stop_ticker()

    def _stop_ticker(self) -> None:
        if self._ticker is not None:
            self._ticker.stop()
            self._ticker = None

    def _tick(self) -> None:
        if not self.active:
            self._stop_ticker()
            return
        self.update(self._renderable())

    def _renderable(self) -> Text:
        active = self.active
        text = Text()
        text.append("✻ ", style=f"bold {_ACCENT}" if active else _DIM)
        text.append("thinking…" if active else "thinking", style=f"italic {_MUTED}")
        duration = self.duration_text
        suffix = f"  ·  {duration}" if duration else ""
        if not self.collapsed:
            if suffix:
                text.append(suffix, style=_DIM)
            text.append("\n")
            for line in self.thinking.splitlines():
                text.append("  ")
                text.append(line, style=f"italic {_MUTED}")
                text.append("\n")
            return text
        preview = _preview_line(self.thinking)
        if preview:
            budget = max(8, self._width() - len(text.plain) - len(suffix) - 2)
            if len(preview) > budget:
                preview = preview[: max(1, budget - 1)] + "…"
            text.append("  ")
            text.append(preview, style=_DIM)
        if suffix:
            text.append(suffix, style=_DIM)
        return text

    def set_expanded(self, expanded: bool) -> None:
        if expanded == (not self.collapsed):
            return
        self.collapsed = not expanded
        if self.is_mounted:
            self.update(self._renderable())

    def set_thinking(
        self,
        thinking: str,
        *,
        started_at: float | None = None,
        seconds: float | None = None,
    ) -> None:
        """Update the reasoning text and its timing (streaming grows both)."""
        if (thinking, started_at, seconds) == (
            self.thinking,
            self.started_at,
            self.seconds,
        ):
            return
        self.thinking = thinking
        self.started_at = started_at
        self.seconds = seconds
        if self.is_mounted:
            self.update(self._renderable())
            self._sync_ticker()

    def action_toggle(self) -> None:
        self.collapsed = not self.collapsed
        self.update(self._renderable())

    def on_click(self) -> None:
        """Click the row to expand/collapse it, like the tool cards."""
        self.action_toggle()


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
