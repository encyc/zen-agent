"""Concrete pickers built on the shared palette skeleton."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label, OptionList

from zenagent.app.models import ModelEntry
from zenagent.app.sessions import SessionEntry
from zenagent.cli.tui.screens.palette import PaletteItem, PaletteScreen

__all__ = [
    "ChoicePicker",
    "Confirm",
    "ModelPicker",
    "RenamePrompt",
    "SessionAction",
    "SessionPicker",
    "ToolApproval",
]


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


@dataclass(frozen=True)
class SessionAction:
    """What the session picker wants the app to do with the highlighted row."""

    kind: str
    path: Path | None = None


class SessionPicker(PaletteScreen[SessionAction]):
    """Pick a saved session; letter keys act on the highlighted one."""

    ACTION_KEYS = {  # noqa: RUF012 - read by PaletteSearch
        "n": "new",
        "f": "fork",
        "d": "delete",
        "e": "export",
        "r": "rename",
    }

    def __init__(self, entries: list[SessionEntry]) -> None:
        self.entries = entries
        items = [
            PaletteItem(
                label=f"{'●' if entry.current else ' '} {entry.title or entry.path.name}",
                value=SessionAction("resume", entry.path),
                note=(
                    f"{entry.messages} msgs · "
                    f"{time.strftime('%m-%d %H:%M', time.localtime(entry.created))}"
                ),
                selected=entry.current,
            )
            for entry in entries
        ]
        super().__init__(
            title="Sessions",
            items=items,
            placeholder="Search sessions",
            footer=(
                "↑↓ move   enter resume   n new   f fork   "
                "d delete   e export   r rename"
            ),
        )

    def _highlighted_path(self) -> Path | None:
        option_list = self.query_one("#palette-list", OptionList)
        index = option_list.highlighted
        if index is None or not 0 <= index < len(self._filtered):
            return None
        return self._filtered[index].value.path

    def action_new(self) -> None:
        self.dismiss(SessionAction("new"))

    def action_fork(self) -> None:
        self.dismiss(SessionAction("fork"))

    def action_delete(self) -> None:
        self.dismiss(SessionAction("delete", self._highlighted_path()))

    def action_export(self) -> None:
        self.dismiss(SessionAction("export", self._highlighted_path()))

    def action_rename(self) -> None:
        self.dismiss(SessionAction("rename", self._highlighted_path()))


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


class RenamePrompt(ModalScreen[str]):
    """A one-line rename dialog for a saved session."""

    BINDINGS = [("escape", "cancel", "Cancel")]  # noqa: RUF012 - Textual class API

    def __init__(self, value: str = "") -> None:
        super().__init__()
        self.initial = value

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal"):
            yield Label("rename session")
            yield Input(value=self.initial, id="rename-input", placeholder="session name")
            yield Label("[enter] save   [esc] cancel")

    def on_mount(self) -> None:
        field = self.query_one("#rename-input", Input)
        field.focus()
        field.cursor_position = len(field.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ToolApproval(ModalScreen[str]):
    """Ask-mode approval for one tool call: allow / always / deny."""

    BINDINGS = [  # noqa: RUF012 - Textual class API
        ("y", "allow", "Allow"),
        ("a", "always", "Always"),
        ("n,escape", "deny", "Deny"),
        ("enter", "allow", "Allow"),
    ]

    def __init__(self, tool_name: str, arguments: dict) -> None:
        super().__init__()
        self.tool_name = tool_name
        self.arguments = arguments

    def compose(self) -> ComposeResult:
        preview = ", ".join(f"{key}={value!r}" for key, value in self.arguments.items())
        if len(preview) > 200:
            preview = preview[:199] + "…"
        with Vertical(classes="modal"):
            yield Label(f"allow {self.tool_name}?")
            if preview:
                yield Label(preview)
            yield Label("[y] allow   [a] allow for this session   [n] deny")

    def action_allow(self) -> None:
        self.dismiss("allow")

    def action_always(self) -> None:
        self.dismiss("always")

    def action_deny(self) -> None:
        self.dismiss("deny")


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


class ChoicePicker(PaletteScreen[str]):
    """Pick one value from a small fixed vocabulary (mode, thinking, ...).

    This is the TUI-side counterpart of argument completion: omp opens a
    selector for stateful enums, and the shared ``app.slash_args`` table feeds
    both paths so they cannot drift.
    """

    def __init__(
        self,
        title: str,
        choices: list[tuple[str, str]],
        selected: str = "",
        placeholder: str = "Search",
    ) -> None:
        items = [
            PaletteItem(
                label=value,
                value=value,
                note=description,
                selected=value == selected,
            )
            for value, description in choices
        ]
        super().__init__(title=title, items=items, placeholder=placeholder)
