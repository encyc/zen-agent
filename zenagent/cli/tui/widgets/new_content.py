"""The "new output below" marker shown when the transcript is pinned.

omp never needs this: committed rows live in the terminal's own scrollback and
the renderer does not track scroll position. Textual owns the viewport, so a
pinned transcript has to say what it is holding back.
"""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

__all__ = ["NewContentBar"]

_ACCENT = "#d9a86c"
_MUTED = "#8296a6"


class NewContentBar(Static):
    """One dim row; hidden unless there is output waiting below."""

    def __init__(self) -> None:
        super().__init__("", id="new-content")
        self.display = False

    def show_marker(self) -> None:
        text = Text()
        text.append("↓ ", style=_ACCENT)
        text.append("new output", style=_MUTED)
        text.append("  ·  pagedown", style="#5d6f7e")
        self.update(text)
        self.display = True

    def hide_marker(self) -> None:
        self.display = False
