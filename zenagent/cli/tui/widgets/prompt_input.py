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

# Application chords the focused TextArea would otherwise swallow (ctrl+d is
# delete-forward, ctrl+k kills the line, ...). omp solves this with an input
# listener chain that keeps app actions authoritative; here the prompt hands
# these keys straight to the app action of the same name.
APP_CHORDS = {
    "escape": "interrupt",
    "ctrl+c": "clear_draft",
    "ctrl+d": "quit_app",
    "ctrl+q": "follow_up",
    "ctrl+p": "cycle_model",
    "alt+p": "pick_model",
    "ctrl+o": "toggle_tools",
    "ctrl+t": "toggle_thinking",
    "ctrl+k": "command_palette",
    "ctrl+b": "toggle_sidebar",
    "ctrl+s": "steer",
    "ctrl+x": "clear_queue",
}


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
        action = APP_CHORDS.get(event.key)
        if action is not None:
            event.stop()
            await self.app.run_action(action)
            return
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
