"""Read-only background-job log overlay.

A job's output is already captured (bounded head+tail, also while running) by
``tools.jobs``; this screen is just the reader the TUI was missing.
"""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Static

from zenagent.app.views import JobLogView
from zenagent.cli.tui import palette
from zenagent.cli.tui.screens.modals import DialogScreen

__all__ = ["JobLogScreen"]

_STATUS_LABELS = {
    "running": "running",
    "ok": "done",
    "failed": "failed",
    "timed_out": "timed out",
}


class JobLogScreen(DialogScreen[None]):
    """``bg_1 · running · 4s`` + the command and its captured output."""

    BINDINGS = [("escape", "close", "Close")]  # noqa: RUF012 - Textual class API

    DEFAULT_CSS = """
    JobLogScreen #job-log-body {
        height: auto;
        max-height: 60vh;
    }
    JobLogScreen .job-log-text {
        height: auto;
    }
    """

    def __init__(self, view: JobLogView) -> None:
        super().__init__()
        self.view = view

    @property
    def job_id(self) -> str:
        return self.view.id

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(Text("job log"), id="dialog-title")
            yield Static(self._header(), id="dialog-body", classes="job-log-text")
            with VerticalScroll(id="job-log-body"):
                yield Static(
                    self._output(), id="job-log-output", classes="job-log-text"
                )
            yield Static(Text("[esc] close"), id="dialog-hint", classes="job-log-text")

    def set_view(self, view: JobLogView) -> None:
        """Swap the snapshot (the 1s poll calls this while the job runs)."""
        self.view = view
        self.query_one("#dialog-body", Static).update(self._header())
        self.query_one("#job-log-output", Static).update(self._output())

    def _header(self) -> Text:
        status = self.view.status
        text = Text()
        text.append(self.view.id, style=palette.TEXT)
        text.append(" · ", style=palette.DIM)
        text.append(
            _STATUS_LABELS.get(status, status),
            style=palette.STATE_COLORS.get(status, palette.MUTED),
        )
        if self.view.duration_ms is not None:
            text.append(f" · {self.view.duration_ms / 1000:.1f}s", style=palette.MUTED)
        if self.view.exit_code is not None:
            text.append(f" · exit {self.view.exit_code}", style=palette.MUTED)
        text.append("\n$ ", style=palette.DIM)
        text.append(self.view.command, style=palette.MUTED)
        return text

    def _output(self) -> Text:
        text = Text(self.view.output or "(no output yet)", style=palette.TEXT)
        if self.view.truncated:
            text.append(
                "\n\n[output truncated: the job exceeded the capture budget]",
                style=palette.DIM,
            )
        return text

    def action_close(self) -> None:
        self.dismiss(None)
