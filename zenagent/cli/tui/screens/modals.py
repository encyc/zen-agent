"""Modal screens: model picker, session picker, confirmation."""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, ListItem, ListView

from zenagent.app.models import ModelEntry
from zenagent.app.sessions import SessionEntry

__all__ = ["Confirm", "ModelPicker", "SessionPicker"]


class ModelPicker(ModalScreen[str | None]):
    """Pick a model; unavailable entries are shown but not selectable."""

    BINDINGS = [("escape", "dismiss", "Cancel")]  # noqa: RUF012 - Textual class API

    def __init__(self, entries: list[ModelEntry]) -> None:
        super().__init__()
        self.entries = entries

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal"):
            yield Label("Models (enter selects, esc cancels)")
            items = []
            for entry in self.entries:
                mark = "● " if entry.current else "  "
                label = (
                    f"{mark}{entry.provider}/{entry.id} · "
                    f"{entry.context_window // 1000}k · "
                    f"{'ready' if entry.available else entry.reason}"
                )
                items.append(ListItem(Label(label), disabled=not entry.available))
            yield ListView(*items, id="model-list")

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        index = event.list_view.index or 0
        entry = self.entries[index]
        self.dismiss(entry.id if entry.available else None)


class SessionPicker(ModalScreen[Path | None]):
    """Pick a saved session to resume."""

    BINDINGS = [("escape", "dismiss", "Cancel")]  # noqa: RUF012 - Textual class API

    def __init__(self, entries: list[SessionEntry]) -> None:
        super().__init__()
        self.entries = entries

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal"):
            yield Label("Sessions (enter resumes, esc cancels)")
            items = []
            for entry in self.entries:
                mark = "● " if entry.current else "  "
                items.append(
                    ListItem(
                        Label(
                            f"{mark}{entry.path.name} · {entry.messages} msgs · "
                            f"{entry.size // 1024}k"
                        )
                    )
                )
            yield ListView(*items, id="session-list")

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        index = event.list_view.index or 0
        self.dismiss(self.entries[index].path)


class Confirm(ModalScreen[bool]):
    """Yes/no confirmation for destructive actions."""

    BINDINGS = [("y", "yes", "Yes"), ("n,escape", "no", "No")]  # noqa: RUF012

    def __init__(self, prompt: str) -> None:
        super().__init__()
        self.prompt = prompt

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal"):
            yield Label(self.prompt)
            yield Label("[y] yes    [n] no")

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)
