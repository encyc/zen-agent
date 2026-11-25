"""Typed transcript rows.

Each row owns one visual role from omp's transcript vocabulary: a filled user
bar, a one-line thinking row that opens on demand, a dim usage tail. Keeping
them as widgets (rather than formatting strings) is what lets the transcript
style them independently and keeps the container dumb.
"""

from __future__ import annotations

import re
import time

from rich.console import Group
from rich.markdown import Markdown
from rich.padding import Padding
from rich.text import Text
from textual.containers import Vertical
from textual.timer import Timer
from textual.widgets import Static

from zenagent.app.views import MessageView, RunStatus
from zenagent.cli.tui import palette
from zenagent.i18n import t

__all__ = [
    "NoticeRow",
    "ReadGroup",
    "ThinkingRow",
    "UsageRow",
    "UserBlock",
    "format_thinking_duration",
]



def _goal_objective(text: str) -> str:
    start = text.find("<objective>")
    if start < 0:
        return text.splitlines()[0] if text else ""
    start += len("<objective>")
    end = text.find("</objective>", start)
    return text[start:end].strip() if end >= 0 else text[start:].strip()


class UserBlock(Static):
    """The user's turn as a filled, full-width bar -- no prefix, no quoting.

    Goal continuation messages are rendered as a compact ``◎ goal · <objective>``
    row instead of the full controller template, so they do not read as user
    input and do not become the session title.
    """

    def __init__(self, text: str, *, goal: bool = False) -> None:
        self._text = text
        if goal:
            objective = _goal_objective(text)
            self._text = f"◎ goal · {objective}" if objective else "◎ goal"
            renderable = Text()
            renderable.append("◎ ", style=palette.ACCENT)
            renderable.append("goal", style=palette.ACCENT)
            if objective:
                renderable.append(" · ", style=palette.DIM)
                renderable.append(objective, style=palette.MUTED)
            super().__init__(renderable, classes="goal-message")
            return
        renderable = Text()
        for index, line in enumerate(text.splitlines() or [""]):
            if index:
                renderable.append("\n")
            if line.startswith("[image ") and line.endswith("]"):
                renderable.append(line, style=palette.DIM)
            else:
                renderable.append(line, style=palette.TEXT)
        super().__init__(renderable, classes="user-message")

    @property
    def plain(self) -> str:
        return self._text


def _preview_line(thinking: str) -> str:
    """The first line with content, its markdown markers dropped.

    The open body *renders* the markdown; the one-line summary must not echo
    the syntax (``**Step 1**`` reads as noise at 96 columns).
    """
    for line in thinking.splitlines():
        if line.strip():
            return _strip_markdown(line.strip())
    return ""


_MD_LEAD = re.compile(r"^(?:#{1,6}\s+|>\s?|[-*+]\s+|\d+[.)]\s+)")
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MD_INLINE = re.compile(r"\*\*|__|~~|`")
_MD_EMPHASIS_OPEN = re.compile(r"(?:^|(?<=[\s(]))[*_](?=\S)")
_MD_EMPHASIS_CLOSE = re.compile(r"(?<=\S)[*_](?=[\s).,;:!?]|$)")


def _strip_markdown(line: str) -> str:
    text = _MD_LEAD.sub("", line)
    text = _MD_LINK.sub(r"\1", text)
    text = _MD_INLINE.sub("", text)
    text = _MD_EMPHASIS_OPEN.sub("", text)
    return _MD_EMPHASIS_CLOSE.sub("", text).strip()


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
        """The row as text: the header, plus the raw body once it is open.

        ``Group`` (the open row) carries no ``plain`` of its own, so this reads
        the pieces rather than the renderable.
        """
        head = self._head().plain
        return head if self.collapsed else f"{head}\n{self.thinking}"

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
        # The constructor rendered before the widget had a width: redo it now,
        # and once more after the first layout (the width can still be 0 here).
        self._sync_classes()
        self.update(self._renderable())
        self.call_after_refresh(lambda: self.update(self._renderable()))
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

    def _renderable(self) -> Text | Group:
        head = self._head()
        if self.collapsed:
            return head
        # The open body is the model's own markdown, rendered rather than
        # echoed: fenced code, lists and emphasis read the way they were
        # written. It keeps the reasoning tier's colour (``style``) so the card
        # still reads as context, one indent step in like the tool bodies.
        body = Markdown(
            self.thinking,
            code_theme="ansi_dark" if palette.is_dark() else "ansi_light",
            style=palette.MUTED,
        )
        return Group(head, Padding(body, (0, 0, 0, 2)))

    def _head(self) -> Text:
        """The one-line header: glyph, label, then the preview or the duration."""
        active = self.active
        text = Text()
        text.append("✻ ", style=f"bold {palette.ACCENT}" if active else palette.DIM)
        thinking = t("tui.rows.thinking_active") if active else t("tui.rows.thinking")
        text.append(thinking, style=f"italic {palette.MUTED}")
        duration = self.duration_text
        suffix = f"  ·  {duration}" if duration else ""
        if not self.collapsed:
            if suffix:
                text.append(suffix, style=palette.DIM)
            return text
        preview = _preview_line(self.thinking)
        if preview:
            budget = max(8, self._width() - len(text.plain) - len(suffix) - 2)
            if len(preview) > budget:
                preview = preview[: max(1, budget - 1)] + "…"
            text.append("  ")
            text.append(preview, style=palette.DIM)
        if suffix:
            text.append(suffix, style=palette.DIM)
        return text

    def _sync_classes(self) -> None:
        """The CSS single-line guarantee follows the expanded/collapsed state."""
        if not self.is_mounted:
            return
        if self.collapsed:
            self.remove_class("expanded")
        else:
            self.add_class("expanded")

    def set_expanded(self, expanded: bool) -> None:
        if expanded == (not self.collapsed):
            return
        self.collapsed = not expanded
        self._sync_classes()
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
        self._sync_classes()
        self.update(self._renderable())

    def on_click(self) -> None:
        """Click the row to expand/collapse it, like the tool cards."""
        self.action_toggle()


class ReadGroup(Vertical):
    """Consecutive ``read_file`` cards folded under one header.

    A turn that reads ten files should not push ten cards into the transcript;
    the group keeps one line until expanded. Cards stay real ``ToolCard``
    widgets (so live progress and per-card expansion still work) — only their
    visibility is group-controlled.
    """

    BINDINGS = [("enter,space", "toggle", "Expand")]  # noqa: RUF012
    can_focus = True

    def __init__(self) -> None:
        super().__init__(classes="read-group")
        self.collapsed = True
        self._cards: list = []
        self._header_text = ""
        self._header = Static("", classes="read-group-header")

    def compose(self):
        yield self._header

    @property
    def cards(self) -> list:
        return list(self._cards)

    @property
    def header_text(self) -> str:
        return self._header_text

    def add_card(self, card) -> None:
        self._cards.append(card)
        self.mount(card)
        card.display = not self.collapsed
        self._refresh()

    def _refresh(self) -> None:
        count = len(self._cards)
        files = len(
            {
                str(card.view.details.get("path") or card.view.args.get("path") or "")
                for card in self._cards
            }
        )
        text = t("tui.read_group.header", count=count, files=files)
        if self.collapsed:
            text += t("tui.read_group.hint")
        self._header_text = text
        self._header.update(Text(text, style=palette.MUTED))

    def action_toggle(self) -> None:
        self.collapsed = not self.collapsed
        for card in self._cards:
            card.display = not self.collapsed
        self._refresh()

    def on_click(self, event=None) -> None:
        # Clicks on a child card belong to that card (expand/diff), not the group.
        widget = getattr(event, "widget", None)
        if widget is not None and widget not in (self, self._header):
            return
        self.action_toggle()


class UsageRow(Static):
    """The dim tail of a finished turn."""

    def __init__(self, status: RunStatus) -> None:
        super().__init__(_usage_text(status), classes="usage-row")

    @property
    def plain(self) -> str:
        # Textual 8 removed ``Static.renderable``; ``content`` is the accessor.
        content = self.content
        return content.plain if isinstance(content, Text) else str(content)


def _usage_text(status: RunStatus) -> Text:
    parts = [
        t("tui.rows.in", n=status.input_tokens),
        t("tui.rows.out", n=status.output_tokens),
    ]
    if status.cache_read:
        parts.append(t("tui.rows.cache", n=status.cache_read))
    parts.append(f"${status.cost:.4f}")
    return Text(t("tui.rows.done") + " · " + " · ".join(parts), style=palette.DIM)


class DividerRow(Static):
    """A dim one-line divider: context was compacted at this point."""

    def __init__(self, text: str) -> None:
        self._text = f"── {text} ──"
        super().__init__(Text(self._text, style=palette.DIM), classes="divider-row")

    @property
    def plain(self) -> str:
        return self._text


class NoticeRow(Static):
    """A quiet one-off line: command output, unknown command, warnings."""

    def __init__(
        self, text: str, *, error: bool = False, warning: bool = False
    ) -> None:
        self._text = text
        if error:
            self.level = "error"
            style = palette.ERROR
        elif warning:
            self.level = "warning"
            style = palette.WARN
        else:
            self.level = "info"
            style = palette.MUTED
        super().__init__(Text(text, style=style), classes="notice")

    @property
    def plain(self) -> str:
        return self._text


def assistant_text(view: MessageView) -> Text:
    """Plain-text fallback for an assistant turn with no markdown renderer."""
    return Text(view.text, style=palette.TEXT)
