"""Concrete pickers built on the shared palette skeleton."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Generic, TypeVar

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static

from zenagent.app.models import ModelEntry
from zenagent.app.sessions import SessionEntry
from zenagent.cli.tui.screens.palette import PaletteItem, PaletteScreen

__all__ = [
    "ChoicePicker",
    "Confirm",
    "DialogChoice",
    "DialogScreen",
    "HistorySearch",
    "ModelPicker",
    "RenamePrompt",
    "SessionAction",
    "SessionPicker",
    "ToolApproval",
]

_T = TypeVar("_T")


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

    BINDINGS = [  # noqa: RUF012 - Textual class API
        ("escape", "cancel", "Cancel"),
        ("tab", "toggle_scope", "All projects"),
    ]

    ACTION_KEYS = {  # noqa: RUF012 - read by PaletteSearch
        "n": "new",
        "f": "fork",
        "d": "delete",
        "e": "export",
        "r": "rename",
        "t": "toggle_scope",
    }

    def __init__(self, entries: list[SessionEntry], *, scope: str = "project") -> None:
        self.entries = entries
        self.scope = scope
        items = []
        for entry in entries:
            marker = f"{'★' if entry.pinned else ' '}{'●' if entry.current else ' '}"
            if scope == "all":
                project_label = entry.project or "legacy"
                label = f"{marker} [{project_label}] {entry.title or entry.path.name}"
            else:
                label = f"{marker} {entry.title or entry.path.name}"
            items.append(
                PaletteItem(
                    label=label,
                    value=SessionAction("resume", entry.path),
                    note=(
                        f"{entry.messages} msgs · "
                        f"{time.strftime('%m-%d %H:%M', time.localtime(entry.created))}"
                    ),
                    selected=entry.current,
                )
            )
        scope_hint = "tab: project sessions" if scope == "all" else "tab: all projects"
        super().__init__(
            title="Sessions",
            items=items,
            placeholder="Search sessions",
            footer=(
                f"↑↓ move   enter resume   n new   f fork   "
                f"d delete   e export   r rename   {scope_hint}"
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

    def action_toggle_scope(self) -> None:
        self.dismiss(SessionAction("toggle-scope"))


class DialogScreen(ModalScreen[_T], Generic[_T]):
    """A centered, framed modal dialog.

    A bare ``ModalScreen`` only dims the screen underneath; the approval,
    confirmation and rename flows also need a panel, spacing, and the same
    title/detail/choice rhythm, so that chrome lives here once.
    """

    DEFAULT_CSS = """
    DialogScreen {
        align: center middle;
        background: #0d1319 65%;
    }

    DialogScreen #dialog {
        width: 90%;
        max-width: 88;
        height: auto;
        padding: 1 2;
        background: #141c24;
        border: round #263441;
    }

    DialogScreen #dialog-title {
        height: auto;
        padding: 0 0 1 0;
        color: #d9a86c;
        text-style: bold;
    }

    DialogScreen #dialog-body {
        height: auto;
        margin: 0 0 1 0;
    }

    DialogScreen .dialog-detail {
        height: auto;
        color: #8296a6;
    }

    DialogScreen .dialog-command,
    DialogScreen .dialog-target {
        height: auto;
        color: #d7e0e8;
    }

    DialogScreen .dialog-note {
        height: auto;
        color: #5d6f7e;
    }

    DialogScreen .dialog-add {
        height: auto;
        color: #8fbf8f;
    }

    DialogScreen .dialog-remove {
        height: auto;
        color: #e08787;
    }

    DialogScreen #dialog-choices {
        height: auto;
    }

    DialogScreen .dialog-choice {
        height: 1;
        color: #8296a6;
    }

    DialogScreen .dialog-choice:hover {
        background: #1b222a;
        color: #d7e0e8;
    }

    DialogScreen .dialog-choice.-primary {
        color: #d9a86c;
    }

    DialogScreen #dialog-hint {
        height: auto;
        margin: 1 0 0 0;
        color: #5d6f7e;
    }

    DialogScreen #rename-input {
        height: 1;
        padding: 0;
        border: none;
        background: #0d1319;
        color: #d7e0e8;
    }
    """


class DialogChoice(Static):
    """One clickable option row; the screen's ``action_<name>`` handles it."""

    def __init__(self, text: Text, action: str, *, primary: bool = False) -> None:
        self.action_name = action
        self._text = text
        classes = "dialog-choice" + (" -primary" if primary else "")
        super().__init__(text, classes=classes)

    @property
    def plain(self) -> str:
        return self._text.plain

    def on_click(self) -> None:
        handler = getattr(self.screen, f"action_{self.action_name}", None)
        if handler is not None:
            handler()


def _choice(key: str, label: str) -> Text:
    """``[y] allow`` -- Text, so the square brackets stay literal markup."""
    return Text(f"[{key}] {label}")


def _clip(text: str, limit: int) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _approval_body(tool_name: str, arguments: dict) -> list[tuple[str, str]]:
    """``(text, css_class)`` rows: what the model wants to run, at a glance."""
    rows: list[tuple[str, str]] = []

    command = arguments.get("command")
    if isinstance(command, str) and command.strip():
        lines = command.rstrip().splitlines()
        rows.append((f"$ {lines[0]}", "dialog-command"))
        rows.extend((f"  {line}", "dialog-command") for line in lines[1:8])
        if len(lines) > 8:
            rows.append((f"  … {len(lines) - 8} more lines", "dialog-note"))

    path = arguments.get("path")
    if isinstance(path, str) and path:
        rows.append((path, "dialog-target"))

    content = arguments.get("content")
    if isinstance(content, str) and content:
        rows.append((f"{len(content.splitlines())} lines to write", "dialog-note"))

    old = arguments.get("old_string")
    new = arguments.get("new_string")
    if isinstance(old, str) and old:
        rows.append((f"- {_clip(old, 100)}", "dialog-remove"))
    if isinstance(new, str) and new:
        rows.append((f"+ {_clip(new, 100)}", "dialog-add"))

    pattern = arguments.get("pattern")
    if isinstance(pattern, str) and pattern:
        rows.append((f"pattern  {pattern}", "dialog-detail"))

    if not rows:
        for key, value in list(arguments.items())[:5]:
            rows.append((f"{key} = {_clip(str(value), 100)}", "dialog-detail"))
        if len(arguments) > 5:
            rows.append((f"… {len(arguments) - 5} more arguments", "dialog-note"))
    return rows


class Confirm(DialogScreen[bool]):
    """Yes/no confirmation for destructive actions."""

    BINDINGS = [("y", "yes", "Yes"), ("n,escape", "no", "No")]  # noqa: RUF012

    def __init__(self, prompt: str) -> None:
        super().__init__()
        self.prompt = prompt

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(Text("confirm"), id="dialog-title")
            with Vertical(id="dialog-body"):
                yield Static(Text(self.prompt), classes="dialog-detail")
            with Vertical(id="dialog-choices"):
                yield DialogChoice(_choice("y", "yes"), "yes", primary=True)
                yield DialogChoice(_choice("n", "no"), "no")

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)


class RenamePrompt(DialogScreen[str]):
    """A one-line rename dialog for a saved session."""

    BINDINGS = [("escape", "cancel", "Cancel")]  # noqa: RUF012 - Textual class API

    def __init__(self, value: str = "") -> None:
        super().__init__()
        self.initial = value

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(Text("rename session"), id="dialog-title")
            with Vertical(id="dialog-body"):
                yield Input(
                    value=self.initial,
                    id="rename-input",
                    placeholder="session name",
                )
            yield Static(Text("[enter] save   [esc] cancel"), id="dialog-hint")

    def on_mount(self) -> None:
        field = self.query_one("#rename-input", Input)
        field.focus()
        field.cursor_position = len(field.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ToolApproval(DialogScreen[str]):
    """Ask-mode approval for one tool call: allow / always / deny.

    Enter deliberately does nothing: approving a mutating tool is an explicit
    act (``y``/``a`` or a click), so a stray Enter cannot grant permission.
    """

    BINDINGS = [  # noqa: RUF012 - Textual class API
        ("y", "allow", "Allow"),
        ("a", "always", "Always"),
        ("n,escape", "deny", "Deny"),
    ]

    def __init__(
        self,
        tool_name: str,
        arguments: dict,
        *,
        timeout: float | None = None,
        target: str | None = None,
    ) -> None:
        super().__init__()
        self.tool_name = tool_name
        self.arguments = arguments
        self.timeout = timeout
        #: The exact scope an "always" answer would remember (path / command).
        self.target = target

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(Text(f"allow {self.tool_name}?"), id="dialog-title")
            body = _approval_body(self.tool_name, self.arguments)
            if body:
                with Vertical(id="dialog-body"):
                    for line, css_class in body:
                        yield Static(Text(line), classes=css_class)
            if self.target:
                yield Static(
                    Text(f"always allows: {self.target}"), id="dialog-scope"
                )
            with Vertical(id="dialog-choices"):
                yield DialogChoice(_choice("y", "allow"), "allow", primary=True)
                yield DialogChoice(_choice("a", "allow for this session"), "always")
                yield DialogChoice(_choice("n", "deny"), "deny")
            if self.timeout:
                yield Static(
                    Text(f"no answer in {self.timeout:g}s = deny"),
                    id="dialog-hint",
                )

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


class HistorySearch(PaletteScreen[str]):
    """Ctrl+R: search submitted prompts, newest first; Enter recalls the text."""

    def __init__(self, entries: list[str]) -> None:
        items = [
            PaletteItem(label=_history_preview(text), value=text)
            for text in entries
        ]
        super().__init__(
            title="History",
            items=items,
            placeholder="Search history",
            footer="↑↓ move   enter recall   esc cancel",
        )


def _history_preview(text: str, limit: int = 100) -> str:
    """One palette row for a possibly multi-line prompt."""
    line = " ⏎ ".join(part.strip() for part in text.splitlines() if part.strip())
    return line if len(line) <= limit else line[: limit - 1] + "…"
