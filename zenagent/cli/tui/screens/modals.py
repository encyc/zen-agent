"""Concrete pickers built on the shared palette skeleton."""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label

from zenagent.app.models import ModelEntry
from zenagent.app.sessions import SessionEntry
from zenagent.cli.tui.screens.palette import PaletteItem, PaletteScreen

__all__ = ["Confirm", "ModelPicker", "SessionPicker"]


class ModelPicker(PaletteScreen[str]):
    """Pick a model; unavailable entries are shown but not selectable."""

    def __init__(self, entries: list[ModelEntry]) -> None:
        self.entries = entries
        items = [
            PaletteItem(
                label=f"{'●' if entry.current else ' '} {entry.provider}/{entry.id}",
                value=entry.id,
                note=(
                    f"{entry.context_window // 1000}k · "
                    f"{'ready' if entry.available else entry.reason}"
                ),
                enabled=entry.available,
                selected=entry.current,
            )
            for entry in entries
        ]
        super().__init__(title="Models", items=items, placeholder="Search models")


class SessionPicker(PaletteScreen[Path]):
    """Pick a saved session to resume."""

    def __init__(self, entries: list[SessionEntry]) -> None:
        self.entries = entries
        items = [
            PaletteItem(
                label=f"{'●' if entry.current else ' '} {entry.path.name}",
                value=entry.path,
                note=f"{entry.messages} msgs · {entry.size // 1024}k",
                selected=entry.current,
            )
            for entry in entries
        ]
        super().__init__(title="Sessions", items=items, placeholder="Search sessions")


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


class SettingsPicker(PaletteScreen[str]):
    """A small settings surface: only the knobs this build can actually change."""

    def __init__(self, thinking: str | None, model: str) -> None:
        from zenagent import settings as settings_module
        from zenagent.cli.tui.screens.palette import PaletteItem

        described = settings_module.describe()
        status = f"{len(described['keys'])} keys · {described['path']}"
        if described["problem"]:
            status = f"⚠ {described['problem']}"
        items = [
            PaletteItem(label="Switch model…", value="model", note=model),
            PaletteItem(label="Settings file", value="noop", note=status, enabled=False),
            PaletteItem(label="Model API keys…", value="model-keys", note="paste a key"),
            PaletteItem(label="Search provider…", value="search-provider", note="default engine"),
            PaletteItem(label="Search API keys…", value="search-keys", note="tavily / brave"),
            PaletteItem(
                label="Thinking level…",
                value="thinking",
                note=thinking or "off",
            ),
            PaletteItem(label="Toggle info sidebar", value="sidebar", note="ctrl+b"),
            PaletteItem(label="Clear conversation", value="clear"),
            PaletteItem(label="Export session as markdown", value="export"),
        ]
        super().__init__(title="Settings", items=items, placeholder="Search settings")
