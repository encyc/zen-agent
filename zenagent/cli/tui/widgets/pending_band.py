"""Dock band listing pending user messages, directly above the composer.

Two lanes share the band: ``queue`` entries wait for the current run to end,
``steer`` entries are delivered at the next step boundary of the running turn.
Each message is one clipped line; anything past :data:`MAX_MESSAGE_ROWS` folds
into a ``… n more`` row. Only queued rows are clickable (they promote to the
steering lane through the app handler) — headers, steering rows and the hint
line are inert.
"""

from __future__ import annotations

from collections.abc import Callable

from rich.text import Text
from textual import events
from textual.widgets import Static

from zenagent.app.views import PendingMessage
from zenagent.cli.tui import palette
from zenagent.cli.tui.textutil import clip_cells
from zenagent.i18n import t

__all__ = ["MAX_MESSAGE_ROWS", "PendingBand"]

#: Message rows the band renders before folding the rest into a summary row.
MAX_MESSAGE_ROWS = 6


def _one_line(text: str) -> str:
    """Fold embedded newlines so one pending message stays one terminal line."""
    return text.replace("\n", " ↵ ")


class PendingBand(Static):
    """``Queued · n`` / ``Steering · n`` groups plus a key hint; hidden if empty."""

    DEFAULT_CSS = """
    PendingBand {
        height: auto;
        background: $surface;
        padding: 0 1;
    }
    """

    def __init__(self) -> None:
        super().__init__("", id="pending-band")
        self._plain = ""
        #: Content row -> queue-lane pending id, for click promotion.
        self._line_to_id: dict[int, int] = {}
        self._on_promote: Callable[[int], None] | None = None
        self.display = False

    @property
    def plain(self) -> str:
        return self._plain

    def set_handler(self, handler: Callable[[int], None] | None) -> None:
        """Install the promote callback (None clears it)."""
        self._on_promote = handler

    def on_click(self, event: events.Click) -> None:
        pending_id = self._line_to_id.get(int(event.y))
        if pending_id is not None and self._on_promote is not None:
            self._on_promote(pending_id)

    def set_pending(self, pending: list[PendingMessage], width: int) -> None:
        """Render one snapshot of ``RunStatus.pending``; empty hides the band."""
        self._line_to_id = {}
        if not pending:
            self._plain = ""
            self.update("")
            self.display = False
            return

        content_width = max(10, width - 2)   # padding: 0 1
        text = Text()
        row = 0
        budget = MAX_MESSAGE_ROWS
        hidden = 0
        for lane in ("queue", "steer"):
            entries = [item for item in pending if item.lane == lane]
            if not entries:
                continue
            title = t(
                "tui.queue.title.queued"
                if lane == "queue"
                else "tui.queue.title.steer"
            )
            text.append(f"{title} · {len(entries)}", style=palette.ACCENT)
            text.append("\n")
            row += 1
            for index, item in enumerate(entries, start=1):
                if budget <= 0:
                    hidden += 1
                    continue
                budget -= 1
                line = f"  {index}. {_one_line(item.text)}"
                if item.images:
                    line += f" · {t('tui.queue.images', n=item.images)}"
                text.append(
                    clip_cells(line, content_width), style=palette.MUTED
                )
                text.append("\n")
                if lane == "queue":
                    self._line_to_id[row] = item.id
                row += 1
        if hidden:
            text.append(t("tui.queue.more", n=hidden), style=palette.DIM)
            text.append("\n")
            row += 1
        text.append(
            clip_cells(t("tui.queue.hint"), content_width), style=palette.DIM
        )
        self._plain = text.plain
        self.update(text)
        self.display = True
