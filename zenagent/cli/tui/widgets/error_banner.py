"""Sticky provider-error banner (omp's ``errorBannerContainer``).

Tool failures already live on their cards; a provider/stream failure used to
scroll away with the transcript. This one-row banner stays in the dock until
the user clicks it away or starts the next run.
"""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

from zenagent.cli.tui import palette

__all__ = ["MAX_MESSAGE_CHARS", "ErrorBanner"]

_ERROR = palette.ERROR

#: Long provider errors would push the prompt off-screen; the tail of a provider
#: error is rarely unique, the head names the cause.
MAX_MESSAGE_CHARS = 160


class ErrorBanner(Static):
    """One red row: ``✘ <message>``. Hidden while there is no error."""

    def __init__(self) -> None:
        self.message = ""
        super().__init__("", id="error-banner")
        self.display = False

    @property
    def plain(self) -> str:
        return self.message

    def set_error(self, message: str) -> None:
        text = (message or "").strip()
        if len(text) > MAX_MESSAGE_CHARS:
            text = text[: MAX_MESSAGE_CHARS - 1] + "…"
        self.message = text
        if not text:
            self.display = False
            self.update("")
            return
        self.display = True
        self.update(Text(f"✘ {text}", style=_ERROR))

    def on_click(self) -> None:
        """Click to dismiss, like omp's dock banner."""
        self.app.dismiss_error()
