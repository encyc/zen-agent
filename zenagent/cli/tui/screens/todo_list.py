"""Read-only todo overlay: the full checklist behind the one-line summary.

The dock HUD and the sidebar row are both summaries -- the HUD clips the
current task to 48 characters. This dialog is where the whole list fits: phase
groups, status marks and the full item text. It is deliberately read-only; the
model owns the checklist.
"""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Static

from zenagent.cli.tui.screens.modals import DialogScreen

__all__ = ["TodoListScreen"]

_TEXT = "#d7e0e8"
_MUTED = "#8296a6"
_DONE = "#8fbf8f"
_BLOCKED = "#e08787"
_ACCENT = "#d9a86c"

#: ``todo`` tool status → the glyph in front of an item.
_MARKS = {
    "done": ("✓", _DONE),
    "in_progress": ("▶", _ACCENT),
    "pending": ("○", _MUTED),
    "blocked": ("⊘", _BLOCKED),
}

_STATUS_LABELS = {"in_progress": "in progress", "blocked": "blocked"}


class TodoListScreen(DialogScreen[None]):
    """The session checklist, phase by phase; esc closes."""

    BINDINGS = [("escape", "close", "Close")]  # noqa: RUF012 - Textual class API

    DEFAULT_CSS = """
    TodoListScreen #todo-body {
        height: auto;
        max-height: 60vh;
    }
    TodoListScreen .todo-phase {
        height: auto;
        padding: 0 0 0 0;
        margin: 0 0 0 0;
        color: #8296a6;
        text-style: bold;
    }
    TodoListScreen .todo-item {
        height: auto;
        padding: 0 0 0 1;
        color: #d7e0e8;
    }
    """

    def __init__(self, phases: list[dict]) -> None:
        super().__init__()
        self.phases = list(phases or [])

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(Text("todo"), id="dialog-title")
            with VerticalScroll(id="todo-body"):
                for phase in self.phases:
                    name = str(phase.get("name") or "Tasks")
                    yield Static(Text(name), classes="todo-phase")
                    for task in phase.get("tasks") or []:
                        yield Static(self._item_text(task), classes="todo-item")
            yield Static(Text("[esc] close"), id="dialog-hint")

    @staticmethod
    def _item_text(task: dict) -> Text:
        status = str(task.get("status") or "pending")
        mark, style = _MARKS.get(status, ("○", _MUTED))
        text = Text(f"{mark} ", style=style)
        text.append(str(task.get("content") or ""), style=_TEXT)
        label = _STATUS_LABELS.get(status)
        if label:
            text.append(f" · {label}", style=_MUTED)
        return text

    def action_close(self) -> None:
        self.dismiss(None)
