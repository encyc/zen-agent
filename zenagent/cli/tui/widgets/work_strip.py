"""Dock strip: "background work is happening, ctrl+b to see it".

The sidebar is the only place background jobs and subagents are listed. While
it is closed, this one self-clearing row is the way to notice them; clicking it
opens the sidebar. It only exists when the sidebar is hidden *and* something is
running, so a quiet session keeps the dock quiet.
"""

from __future__ import annotations

from collections.abc import Callable

from rich.text import Text
from textual.widgets import Static

from zenagent.cli.tui import palette

__all__ = ["WorkStrip"]

_ACCENT = palette.ACCENT
_MUTED = palette.MUTED
_DIM = palette.DIM


class WorkStrip(Static):
    """``◐ 2 jobs · 1 task · ctrl+b``; hidden unless work runs sidebar-closed."""

    DEFAULT_CSS = """
    WorkStrip {
        height: 1;
        background: #141c24;
        padding: 0 1;
    }
    """

    def __init__(self) -> None:
        super().__init__("", id="work-strip")
        self._plain = ""
        self._on_click_handler: Callable[[], None] | None = None
        self.display = False

    @property
    def plain(self) -> str:
        return self._plain

    def set_handler(self, handler: Callable[[], None] | None) -> None:
        """Install the click handler (None clears it)."""
        self._on_click_handler = handler

    def set_work(
        self, jobs_running: int, task: str | None, *, sidebar_hidden: bool
    ) -> None:
        """Render one work summary; empty work (or a visible sidebar) hides it."""
        if not sidebar_hidden or (jobs_running <= 0 and not task):
            self._plain = ""
            self.update("")
            self.display = False
            return
        parts: list[str] = []
        if jobs_running:
            noun = "job" if jobs_running == 1 else "jobs"
            parts.append(f"{jobs_running} {noun}")
        if task:
            parts.append("1 task")
        text = Text()
        text.append("◐ ", style=_ACCENT)
        text.append(" · ".join(parts), style=_MUTED)
        text.append(" · ctrl+b", style=_DIM)
        self._plain = text.plain
        self.update(text)
        self.display = True

    def on_click(self) -> None:
        if self._on_click_handler is not None:
            self._on_click_handler()
