"""The "working" badge that sits at the right end of the prompt row.

omp keeps the run state in the status band, but the band is a whole line away
from where the eye sits while typing; a small spinner next to the caret says
"the agent is still going" without moving anything else.
"""

from __future__ import annotations

import time

from rich.text import Text
from textual.widgets import Static

from zenagent.cli.tui import palette

__all__ = ["WorkIndicator"]

_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_ACCENT = palette.ACCENT


class WorkIndicator(Static):
    """`⠋ working 3s` while a run is active; hidden otherwise."""

    def __init__(self) -> None:
        super().__init__("", id="work-indicator")
        self.display = False
        self._frame = 0
        self._last = ""
        self._started: float | None = None
        self._timer = None

    @property
    def plain(self) -> str:
        return self._last

    @property
    def running(self) -> bool:
        return self._started is not None

    def set_running(self, running: bool) -> None:
        if running and self._started is None:
            self._started = time.monotonic()
            self.display = True
            self._timer = self.set_interval(0.12, self._tick)
            self._tick()
        elif not running and self._started is not None:
            self._started = None
            if self._timer is not None:
                self._timer.stop()
                self._timer = None
            self.display = False
            self._last = ""

    def _tick(self) -> None:
        if self._started is None:
            return
        self._frame = (self._frame + 1) % len(_FRAMES)
        elapsed = int(time.monotonic() - self._started)
        text = Text()
        text.append(f"{_FRAMES[self._frame]} ", style=_ACCENT)
        text.append("working", style=_ACCENT)
        if elapsed >= 1:
            text.append(f" {elapsed}s", style="#8296a6")
        self._last = text.plain
        self.update(text)
