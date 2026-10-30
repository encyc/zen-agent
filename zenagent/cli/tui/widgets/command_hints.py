"""Inline slash-command hints shown above the prompt.

omp renders its autocomplete under the editor; our editor sits on the last row,
so the list goes directly above the status band. It is self-drawn (no list
frame) to stay consistent with the rest of the quiet chrome.
"""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

__all__ = ["CommandHints"]

MAX_ROWS = 6

_TEXT = "#d7e0e8"
_MUTED = "#8296a6"
_DIM = "#5d6f7e"
_ACCENT = "#d9a86c"


class CommandHints(Static):
    """A quiet list of matching commands; hidden when there is nothing to show."""

    def __init__(self) -> None:
        super().__init__("", id="command-hints")
        self.display = False
        self._matches: list[tuple[str, str]] = []
        self._index = 0

    # -- state --------------------------------------------------------------

    @property
    def matches(self) -> list[tuple[str, str]]:
        return list(self._matches)

    @property
    def selected(self) -> str | None:
        if not self._matches:
            return None
        return self._matches[self._index][0]

    @property
    def plain(self) -> str:
        return self._renderable().plain

    @property
    def active(self) -> bool:
        return bool(self._matches) and self.display

    def show(self, matches: list[tuple[str, str]]) -> None:
        self._matches = matches[:MAX_ROWS]
        self._index = 0
        if not self._matches:
            self.hide()
            return
        self.display = True
        self.update(self._renderable())

    def hide(self) -> None:
        self._matches = []
        self._index = 0
        self.display = False

    def move(self, delta: int) -> None:
        if not self._matches:
            return
        self._index = (self._index + delta) % len(self._matches)
        self.update(self._renderable())

    # -- rendering ----------------------------------------------------------

    def _renderable(self) -> Text:
        width = max(24, (self.size.width or 80) - 2)
        text = Text()
        for index, (name, description) in enumerate(self._matches):
            if index:
                text.append("\n")
            selected = index == self._index
            text.append("▸ " if selected else "  ", style=_ACCENT if selected else _DIM)
            label = f"/{name}"
            text.append(label, style=f"bold {_ACCENT}" if selected else _TEXT)
            budget = width - len(label) - 4
            if description and budget > 8:
                note = description if len(description) <= budget else description[: budget - 1] + "…"
                text.append("  ")
                text.append(note, style=_MUTED)
        return text
