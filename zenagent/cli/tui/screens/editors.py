"""Settings, hotkeys, usage, MCP and todo editor screens (moved from modals.py, audit P2)."""

from __future__ import annotations

import copy
from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from zenagent.cli.tui.screens.dialogs import (
    DialogScreen,
)
from zenagent.cli.tui.screens.palette import PaletteItem, PaletteScreen
from zenagent.i18n import t


class SettingsPicker(PaletteScreen[str]):
    """A small settings surface: only the knobs this build can actually change."""

    def __init__(
        self,
        thinking: str | None,
        model: str,
        theme: str = "auto",
        language: str = "auto",
    ) -> None:
        from zenagent import settings as settings_module
        from zenagent.cli.tui.screens.palette import PaletteItem

        described = settings_module.describe()
        status = t(
            "tui.settings.file_status",
            n=len(described["keys"]),
            path=described["path"],
        )
        if described["problem"]:
            status = t("tui.settings.file_problem", problem=described["problem"])
        items = [
            PaletteItem(
                label=t("tui.settings.switch_model"), value="model", note=model
            ),
            PaletteItem(
                label=t("tui.settings.theme"), value="theme", note=theme
            ),
            PaletteItem(
                label=t("tui.settings.language"), value="language", note=language
            ),
            PaletteItem(
                label=t("tui.settings.file"),
                value="noop",
                note=status,
                enabled=False,
            ),
            PaletteItem(
                label=t("tui.settings.model_keys"),
                value="model-keys",
                note=t("tui.settings.paste_key"),
            ),
            PaletteItem(
                label=t("tui.settings.search_provider"),
                value="search-provider",
                note=t("tui.settings.default_engine"),
            ),
            PaletteItem(
                label=t("tui.settings.search_keys"),
                value="search-keys",
                note=t("tui.settings.search_keys_note"),
            ),
            PaletteItem(
                label=t("tui.settings.thinking"),
                value="thinking",
                note=thinking or t("tui.settings.off"),
            ),
            PaletteItem(
                label=t("tui.settings.sidebar"), value="sidebar", note="ctrl+b"
            ),
            PaletteItem(label=t("tui.settings.clear"), value="clear"),
            PaletteItem(label=t("tui.settings.export"), value="export"),
        ]
        super().__init__(
            title=t("tui.picker.settings"),
            items=items,
            placeholder=t("tui.picker.settings_search"),
        )



class HotkeysSheet(DialogScreen[None]):
    """Read-only overlay listing the active key bindings."""

    BINDINGS = [("escape", "close", "Close")]  # noqa: RUF012

    def __init__(self, lines: list[str]) -> None:
        super().__init__()
        self.lines = lines

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(Text(t("tui.dialog.hotkeys")), id="dialog-title")
            with VerticalScroll(id="dialog-body"):
                for line in self.lines:
                    yield Static(Text(line), classes="dialog-detail")
            yield Static(Text(t("tui.dialog.close_hint")), id="dialog-hint")

    def action_close(self) -> None:
        self.dismiss(None)


class UsageDashboard(DialogScreen[None]):
    """Read-only usage report: current session plus recent-session totals."""

    BINDINGS = [("escape", "close", "Close")]  # noqa: RUF012

    def __init__(self, lines: list[str]) -> None:
        super().__init__()
        self.lines = lines

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(Text(t("tui.dialog.usage")), id="dialog-title")
            with VerticalScroll(id="dialog-body"):
                for line in self.lines:
                    yield Static(Text(line), classes="dialog-detail")
            yield Static(Text(t("tui.dialog.close_hint")), id="dialog-hint")

    def action_close(self) -> None:
        self.dismiss(None)


class McpAdd(DialogScreen[tuple[str, str] | None]):
    """Two-field wizard: server name + command; the app writes mcp.json."""

    BINDINGS = [("escape", "cancel", "Cancel")]  # noqa: RUF012

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(Text(t("tui.dialog.mcp_add")), id="dialog-title")
            with Vertical(id="dialog-body"):
                yield Input(placeholder=t("tui.dialog.mcp_name"), id="mcp-name")
                yield Input(
                    placeholder=t("tui.dialog.mcp_command"), id="mcp-command"
                )
                yield Static(
                    Text(t("tui.dialog.mcp_hint")), classes="dialog-detail"
                )
            yield Static(Text(t("tui.dialog.save_cancel")), id="dialog-hint")

    def on_mount(self) -> None:
        self.query_one("#mcp-name", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "mcp-name":
            self.query_one("#mcp-command", Input).focus()
            return
        name = self.query_one("#mcp-name", Input).value.strip()
        command = self.query_one("#mcp-command", Input).value.strip()
        if not name or not command:
            return
        self.dismiss((name, command))

    def action_cancel(self) -> None:
        self.dismiss(None)



class TodoEditor(DialogScreen[list[dict] | None]):
    """Editable todo list sharing the model's ``<session>.todo.json`` sidecar."""

    BINDINGS = [  # noqa: RUF012 - Textual class API
        ("a", "add", "Add"),
        ("d", "done", "Done"),
        ("b", "block", "Block"),
        ("u", "unblock", "Unblock"),
        ("x", "delete", "Delete"),
        ("escape", "save", "Save"),
    ]

    MARKERS: ClassVar[dict[str, str]] = {
        "pending": "[ ]",
        "in_progress": "[~]",
        "completed": "[x]",
        "abandoned": "[-]",
        "blocked": "[!]",
    }

    def __init__(self, phases: list[dict] | None = None) -> None:
        super().__init__()
        self.phases = copy.deepcopy(phases or []) or [
            {"name": t("tui.todo.default_phase"), "tasks": []}
        ]

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(Text(t("tui.dialog.todo_edit")), id="dialog-title")
            with Vertical(id="dialog-body"):
                yield Input(placeholder=t("tui.dialog.todo_new"), id="todo-new")
                yield OptionList(id="todo-list")
            yield Static(Text(t("tui.dialog.todo_hint")), id="dialog-hint")

    def on_mount(self) -> None:
        self._refresh()
        self.query_one("#todo-new", Input).focus()

    def _flatten(self) -> list[tuple[int, int, dict]]:
        rows: list[tuple[int, int, dict]] = []
        for phase_index, phase in enumerate(self.phases):
            for task_index, task in enumerate(phase.get("tasks") or []):
                if isinstance(task, dict):
                    rows.append((phase_index, task_index, task))
        return rows

    def _refresh(self) -> None:
        option_list = self.query_one("#todo-list", OptionList)
        option_list.clear_options()
        rows = self._flatten()
        if not rows:
            option_list.add_option(Option(t("tui.todo.empty"), disabled=True))
            return
        for _phase_index, _task_index, task in rows:
            marker = self.MARKERS.get(str(task.get("status", "pending")), "[ ]")
            option_list.add_options(
                [Option(f"{marker} {task.get('content', '')}")]
            )

    def _selected(self) -> tuple[int, int, dict] | None:
        option_list = self.query_one("#todo-list", OptionList)
        rows = self._flatten()
        index = option_list.highlighted
        if index is None or index < 0 or index >= len(rows):
            return None
        return rows[index]

    def _set_status(self, status: str) -> None:
        selected = self._selected()
        if selected is None:
            return
        phase_index, task_index, _task = selected
        self.phases[phase_index]["tasks"][task_index]["status"] = status
        self._refresh()

    def action_add(self) -> None:
        field = self.query_one("#todo-new", Input)
        content = field.value.strip()
        if not content:
            return
        self.phases[0].setdefault("tasks", []).append(
            {"content": content, "status": "pending"}
        )
        field.value = ""
        self._refresh()

    def action_done(self) -> None:
        self._set_status("completed")

    def action_block(self) -> None:
        self._set_status("blocked")

    def action_unblock(self) -> None:
        self._set_status("pending")

    def action_delete(self) -> None:
        selected = self._selected()
        if selected is None:
            return
        phase_index, task_index, _task = selected
        del self.phases[phase_index]["tasks"][task_index]
        self._refresh()

    def action_save(self) -> None:
        self.dismiss(self.phases)


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
        placeholder: str | None = None,
    ) -> None:
        if placeholder is None:
            placeholder = t("tui.picker.search")
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
            title=t("tui.picker.history"),
            items=items,
            placeholder=t("tui.picker.history_search"),
            footer=t("tui.picker.history_footer"),
        )


def _history_preview(text: str, limit: int = 100) -> str:
    """One palette row for a possibly multi-line prompt."""
    line = " ⏎ ".join(part.strip() for part in text.splitlines() if part.strip())
    return line if len(line) <= limit else line[: limit - 1] + "…"
