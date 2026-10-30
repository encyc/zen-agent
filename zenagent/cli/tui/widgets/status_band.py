"""Status band: the filled one-row strip that sits above the prompt.

omp's default ``band`` composer shape puts the whole status line flush against
the left edge as a filled band above an unboxed prompt -- there is no bottom
bar. The line itself is an *ordered segment list* (see
:mod:`zenagent.cli.tui.status`), so facts drop away as the terminal narrows
instead of the whole line being clipped.
"""

from __future__ import annotations

from textual.widgets import Static

from zenagent.app.views import RunStatus
from zenagent.cli.tui.status import render_status_line

__all__ = ["StatusBand"]


class StatusBand(Static):
    """One filled row; degrades by dropping segments as the terminal narrows."""

    def __init__(self, preset: str = "default") -> None:
        super().__init__("", id="status-band")
        self.preset = preset
        self._status: RunStatus | None = None
        self._plain = ""

    @property
    def plain(self) -> str:
        return self._plain

    def update_status(self, status: RunStatus, width: int = 200) -> None:
        self._status = status
        text, plain = render_status_line(status, width, preset=self.preset)
        self._plain = plain
        self.update(text)
