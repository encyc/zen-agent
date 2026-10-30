"""Bottom status bar: model, thinking, tokens, cost and context."""

from __future__ import annotations

from textual.widgets import Static

from zenagent.app.views import RunStatus

__all__ = ["StatusBar"]


class StatusBar(Static):
    """A one-line, always-visible snapshot of the run."""

    def __init__(self, status: RunStatus | None = None) -> None:
        super().__init__("")
        self._status = status
        self._plain = ""

    @property
    def plain(self) -> str:
        return self._plain

    def update_status(self, status: RunStatus) -> None:
        self._status = status
        self._plain = self._format(status)
        self.update(self._plain)

    def _format(self, status: RunStatus) -> str:
        used = status.context_used
        window = status.context_window or 1
        percent = round(used * 100 / window)
        parts = [
            status.model_id,
            f"thinking {status.thinking or 'off'}",
            f"{status.input_tokens} in / {status.output_tokens} out",
            f"${status.cost:.4f}",
            f"ctx {percent}%",
        ]
        if status.follow_ups:
            parts.append(f"queued {status.follow_ups}")
        if status.steering:
            parts.append(f"steering {status.steering}")
        if status.running:
            parts.append("running")
        return " · ".join(parts)
