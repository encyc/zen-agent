"""Multi-line prompt input: Enter submits, Shift+Enter inserts a newline.

Borderless on purpose: the omp ``band`` composer has no frame, only the
``╰─ `` cue drawn by :class:`PromptGutter` in the first three columns (which is
why the text carries a matching left padding).
"""

from __future__ import annotations

from textual import events
from textual.message import Message
from textual.widgets import TextArea

__all__ = ["PromptInput"]

MAX_ROWS = 8


class PromptInput(TextArea):
    """TextArea with chat-style submit semantics and auto growth."""

    MAX_ROWS = MAX_ROWS

    class Submitted(Message):
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    class Resized(Message):
        """Posted when the number of visible prompt rows changes."""

        def __init__(self, rows: int) -> None:
            super().__init__()
            self.rows = rows

    def __init__(self, placeholder: str = "Type a message…") -> None:
        super().__init__(text="", placeholder=placeholder, id="prompt-input")
        self._rows = 1

    @property
    def rows(self) -> int:
        return self._rows

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        rows = max(1, min(self.document.line_count, self.MAX_ROWS))
        if rows != self._rows:
            self._rows = rows
            self.styles.height = rows
            self.post_message(self.Resized(rows))

    async def _on_key(self, event: events.Key) -> None:
        if event.key == "enter":
            event.stop()
            text = self.text.strip()
            if text:
                self.clear()
                self.post_message(self.Submitted(text))
            return
        if event.key == "shift+enter":
            event.stop()
            self.insert("\n")
            return
        await super()._on_key(event)
