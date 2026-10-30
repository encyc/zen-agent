"""Multi-line prompt input: Enter submits, Shift+Enter inserts a newline."""

from __future__ import annotations

from textual import events
from textual.message import Message
from textual.widgets import TextArea

__all__ = ["PromptInput"]


class PromptInput(TextArea):
    """TextArea with chat-style submit semantics."""

    class Submitted(Message):
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    def __init__(self, placeholder: str = "Type a message…") -> None:
        super().__init__(text="", placeholder=placeholder, id="prompt-input")

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
