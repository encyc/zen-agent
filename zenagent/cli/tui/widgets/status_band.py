"""Status band: the filled one-row strip that sits above the prompt.

omp's default ``band`` composer shape puts the whole status line flush against
the left edge as a filled band above an unboxed prompt -- there is no bottom
bar. This widget keeps the ``plain`` contract the exit summary reads.
"""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

from zenagent.app.views import RunStatus

__all__ = ["StatusBand"]

_TEXT = "#d7e0e8"
_MUTED = "#8296a6"
_ACCENT = "#d9a86c"
_ERROR = "#e08787"


class StatusBand(Static):
    """One filled row; degrades by dropping segments as the terminal narrows."""

    def __init__(self) -> None:
        super().__init__("", id="status-band")
        self._status: RunStatus | None = None
        self._plain = ""

    @property
    def plain(self) -> str:
        return self._plain

    def update_status(self, status: RunStatus, width: int = 200) -> None:
        self._status = status
        text, plain = self._format(status, width)
        self._plain = plain
        self.update(text)

    def _format(self, status: RunStatus, width: int = 200) -> tuple[Text, str]:
        used = status.context_used
        window = status.context_window or 1
        percent = round(used * 100 / window)

        text = Text()
        text.append(" ")
        text.append(status.model_id, style=f"bold {_ACCENT}")

        items: list[str] = []
        if width >= 100:
            items.append(f"thinking {status.thinking or 'off'}")
            items.append(f"{status.input_tokens} in / {status.output_tokens} out")
        items.append(f"${status.cost:.4f}")
        items.append(f"context {percent}%")
        if status.follow_ups:
            items.append(f"queued {status.follow_ups}")
        if status.steering:
            items.append(f"steering {status.steering}")

        for item in items:
            text.append("  ")
            text.append(item, style=_MUTED)

        if status.running:
            text.append("  ")
            text.append("running", style=_ACCENT)

        plain = " · ".join([status.model_id, *items])
        if status.running:
            plain += " · running"
        return text, plain
