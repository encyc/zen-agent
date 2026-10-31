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

from zenagent.cli.tui import palette
from zenagent.cli.tui.screens.modals import DialogScreen
from zenagent.i18n import t

__all__ = ["TodoListScreen"]


#: ``todo`` tool status → the glyph in front of an item.
_MARKS = {
    "done": ("✓", palette.OK),
    "in_progress": ("▶", palette.ACCENT),
    "pending": ("○", palette.MUTED),
    "blocked": ("⊘", palette.ERROR),
}

_STATUS_KEYS = {
    "in_progress": "tui.todo.status_in_progress",
    "blocked": "tui.todo.status_blocked",
}


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
        color: $muted;
        text-style: bold;
    }
    TodoListScreen .todo-item {
        height: auto;
        padding: 0 0 0 1;
        color: $foreground;
    }
    """

    def __init__(self, phases: list[dict]) -> None:
        super().__init__()
        self.phases = list(phases or [])

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(Text(t("tui.todo.title")), id="dialog-title")
            with VerticalScroll(id="todo-body"):
                for phase in self.phases:
                    name = str(phase.get("name") or t("tui.todo.tasks"))
                    yield Static(Text(name), classes="todo-phase")
                    for task in phase.get("tasks") or []:
                        yield Static(self._item_text(task), classes="todo-item")
            yield Static(Text(t("tui.dialog.close")), id="dialog-hint")

    @staticmethod
    def _item_text(task: dict) -> Text:
        status = str(task.get("status") or "pending")
        mark, style = _MARKS.get(status, ("○", palette.MUTED))
        text = Text(f"{mark} ", style=style)
        text.append(str(task.get("content") or ""), style=palette.TEXT)
        status_key = _STATUS_KEYS.get(status)
        if status_key:
            text.append(f" · {t(status_key)}", style=palette.MUTED)
        return text

    def action_close(self) -> None:
        self.dismiss(None)
