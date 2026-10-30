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
    "ctrl+r": "history_search",
    "ctrl+b": "toggle_sidebar",
    "ctrl+s": "steer",
    "ctrl+x": "clear_queue",
    "alt+up": "dequeue",
    "pageup": "scroll_up",
    "pagedown": "scroll_down",
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

    def _set_rows(self, rows: int) -> None:
        if rows == self._rows:
            return
        self._rows = rows
        self.styles.height = rows
        self.post_message(self.Resized(rows))

    @property
    def rows(self) -> int:
        return self._rows

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        self._set_rows(max(1, min(self.document.line_count, self.MAX_ROWS)))

    def _hints_active(self) -> bool:
        hints = getattr(self.app, "command_hints", None)
        return bool(hints is not None and hints.active)

    async def _on_key(self, event: events.Key) -> None:
        # `prevent_default` is load-bearing: `stop()` alone still lets Textual run
        # TextArea's default insert, which is how a submitted prompt came back as
        # a stray "\n" on a second row.
        if self._hints_active() and await self._handle_hint_key(event):
            return
        action = APP_CHORDS.get(event.key)
        if action is not None:
            event.stop()
            event.prevent_default()
            await self.app.run_action(action)
            return
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            text = self.text.strip()
            if text:
                self.clear()
                self._set_rows(1)
                self.post_message(self.Submitted(text))
            return
        if event.key == "shift+enter":
            event.stop()
            event.prevent_default()
            self.insert("\n")
            return
        await super()._on_key(event)

    async def _handle_hint_key(self, event: events.Key) -> bool:
        """Arrows/tab/enter/esc belong to the suggestion list while it is open.

        ``Tab`` completes the highlighted candidate into the draft and stays
        there; ``Enter`` completes it *and* submits -- so a value hint executes
        immediately (``/mode `` + Enter applies the highlighted mode), while a
        command-name hint still reaches the command's own picker (``/mode`` +
        Enter opens the mode selector). This avoids the old three-Enter detour
        through the picker just to choose a value that was already highlighted.
        """
        if event.key in ("up", "down"):
            event.stop()
            event.prevent_default()
            await self.app.run_action("hint_prev" if event.key == "up" else "hint_next")
            return True
        if event.key == "tab":
            event.stop()
            event.prevent_default()
            await self.app.run_action("hint_accept")
            return True
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            await self.app.run_action("hint_accept")
            text = self.text.strip()
            if text:
                self.clear()
                self._set_rows(1)
                self.post_message(self.Submitted(text))
            return True
        if event.key == "escape":
            event.stop()
            event.prevent_default()
            await self.app.run_action("hint_dismiss")
            return True
        return False
