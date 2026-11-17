"""Settings, hotkeys, usage, MCP and todo editor screens (moved from modals.py, audit P2)."""

from __future__ import annotations

import copy
from typing import ClassVar

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from zenagent.cli.tui import palette
from zenagent.cli.tui.screens.dialogs import (
    DialogScreen,
)
from zenagent.cli.tui.screens.palette import PaletteItem, PaletteScreen
from zenagent.i18n import t

__all__ = ["HotkeysSheet", "SettingsPanel"]


class SettingsPanel(ModalScreen[None]):
    """The settings dialog: a section rail on the left, the knobs on the right.

    The desktop's settings page is a searchable rail plus one decision tile;
    this is its terminal shape. Values are read live on every render, so a
    flipped toggle or a sub-picker's result shows up without reopening.
    Activation goes through the one ``on_action`` handler the app supplies —
    the same mapping the old flat picker and the REPL text form use.
    """

    BINDINGS = [  # noqa: RUF012 - Textual class API
        ("escape", "close", "Close"),
        ("right", "next_section", "Next section"),
        ("left", "previous_section", "Previous section"),
    ]

    #: The label column's width; values start aligned after it.
    _LABEL_WIDTH: ClassVar[int] = 28

    DEFAULT_CSS = """
    SettingsPanel {
        width: 100%;
        height: 100%;
        padding: 1 2;
        background: $surface;
    }

    SettingsPanel #settings-title {
        height: 1;
        color: $accent;
        text-style: bold;
    }

    SettingsPanel #settings-tabs {
        height: 1;
        margin: 0 0 1 0;
    }

    SettingsPanel #settings-items {
        height: 1fr;
        background: $surface;
        border: none;
        padding: 0;
        scrollbar-size-vertical: 1;
    }

    SettingsPanel OptionList > .option-list--option-highlighted {
        background: $line;
        color: $foreground;
    }

    SettingsPanel #settings-desc {
        height: 1;
        margin: 1 0 0 0;
        color: $dim;
    }

    SettingsPanel #settings-footer {
        height: 1;
        color: $dim;
    }
    """

    #: Sections in display order; each entry is (value, label-i18n-key).
    #: The item values are the dispatch vocabulary every frontend shares.
    SECTIONS: ClassVar[list[tuple[str, str, tuple[tuple[str, str], ...]]]] = [
        (
            "appearance",
            "tui.settings.section_appearance",
            (("theme", "tui.settings.theme"), ("language", "tui.settings.language")),
        ),
        (
            "model",
            "tui.settings.section_model",
            (
                ("model", "tui.settings.switch_model"),
                ("thinking", "tui.settings.thinking"),
                ("model-keys", "tui.settings.model_keys"),
            ),
        ),
        (
            "search",
            "tui.settings.section_search",
            (
                ("search-provider", "tui.settings.search_provider"),
                ("search-keys", "tui.settings.search_keys"),
            ),
        ),
        (
            "session",
            "tui.settings.section_session",
            (
                ("goal-pause", "tui.settings.goal_pause"),
                ("sidebar", "tui.settings.sidebar"),
                ("clear", "tui.settings.clear"),
                ("export", "tui.settings.export"),
            ),
        ),
    ]

    def __init__(self, app, on_action) -> None:
        super().__init__()
        self._app = app
        self._on_action = on_action
        self._section_index = 0
        #: The composed tab line / description; tests read these instead of
        #: Static internals (Textual 8 exposes no renderable accessor).
        self._tabs_line: Text = Text()
        self._description: str = ""

    # -- data ---------------------------------------------------------------

    def _live_note(self, value: str) -> str:
        """The current value a row shows; read on every render."""
        from zenagent import settings as settings_module

        if value == "model":
            return self._app.models.current()
        if value == "thinking":
            return (
                getattr(self._app.runtime.agent, "thinking", None)
                or t("tui.settings.off")
            )
        if value == "theme":
            return self._app._theme_spec
        if value == "language":
            return self._app._language_spec
        if value == "goal-pause":
            return t(
                "tui.settings.goal_pause_on"
                if settings_module.get_pref("goal_pause_on_session_switch", False)
                else "tui.settings.goal_pause_off"
            )
        if value == "sidebar":
            return "ctrl+b"
        if value == "search-provider":
            return t("tui.settings.default_engine")
        if value == "search-keys":
            return t("tui.settings.search_keys_note")
        if value == "model-keys":
            return t("tui.settings.paste_key")
        return ""

    def items_for(self, section_value: str) -> list[PaletteItem]:
        """The rows of one section, with live notes."""
        section = next(s for s in self.SECTIONS if s[0] == section_value)
        return [
            PaletteItem(
                label=t(label_key),
                value=value,
                note=self._live_note(value),
                enabled=True,
            )
            for value, label_key in section[2]
        ]

    @property
    def items(self) -> list[PaletteItem]:
        """Every row across sections, in display order (tests, hints)."""
        rows: list[PaletteItem] = []
        for section_value, _, _ in self.SECTIONS:
            rows.extend(self.items_for(section_value))
        return rows

    # -- layout -------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Static(t("tui.picker.settings"), id="settings-title")
        yield Static(id="settings-tabs")
        yield OptionList(id="settings-items")
        yield Static("", id="settings-desc")
        yield Static(self._footer_text(), id="settings-footer")

    def on_mount(self) -> None:
        self._show_section(0)
        self.query_one("#settings-items", OptionList).focus()

    def _tabs_text(self) -> Text:
        """One rich line of tabs; the selected one is inverted like omp's."""
        line = Text()
        for index, (_, title_key, _) in enumerate(self.SECTIONS):
            title = t(title_key)
            if index == self._section_index:
                line.append(f" {title} ", style=palette.ACCENT + " bold")
            else:
                line.append(f" {title} ", style=palette.DIM)
            line.append("  ", style=palette.DIM)
        return line

    def _footer_text(self) -> str:
        from zenagent import settings as settings_module

        described = settings_module.describe()
        if described["problem"]:
            status = t("tui.settings.file_problem", problem=described["problem"])
        else:
            status = t(
                "tui.settings.file_status",
                n=len(described["keys"]),
                path=described["path"],
            )
        return f"{status} · {t('tui.settings.panel_hint')}"

    def _row_text(self, item: PaletteItem) -> Text:
        """Label and value as two aligned columns, like the desktop page."""
        from zenagent.cli.tui.textutil import clip_cells

        line = Text()
        line.append(f"{clip_cells(item.label, self._LABEL_WIDTH):<{self._LABEL_WIDTH}}")
        line.append(item.note or "", style=palette.TEXT)
        return line

    def _show_section(self, index: int) -> None:
        self._section_index = index
        self._tabs_line = self._tabs_text()
        self.query_one("#settings-tabs", Static).update(self._tabs_line)
        items = self.query_one("#settings-items", OptionList)
        items.clear_options()
        section_value = self.SECTIONS[index][0]
        for item in self.items_for(section_value):
            items.add_option(Option(self._row_text(item)))
        if items.option_count:
            items.highlighted = 0
        self._update_description()

    def _update_description(self) -> None:
        """The highlighted row's description, the line above the footer."""
        section_value = self.SECTIONS[self._section_index][0]
        rows = self.items_for(section_value)
        items = self.query_one("#settings-items", OptionList)
        index = items.highlighted
        description = ""
        if index is not None and 0 <= index < len(rows):
            description = t(f"tui.settings.desc.{rows[index].value}")
            if description.startswith("tui.settings.desc."):
                description = ""  # no description key for this row
        self._description = description
        self.query_one("#settings-desc", Static).update(description)

    # -- navigation ---------------------------------------------------------

    @on(OptionList.OptionHighlighted, "#settings-items")
    def _highlight_changed(self, _event) -> None:
        self._update_description()

    @on(OptionList.OptionSelected, "#settings-items")
    def _item_selected(self, _event: OptionList.OptionSelected) -> None:
        self._activate_highlighted()

    def action_next_section(self) -> None:
        """Right/`l`: the next tab, wrapping; the list follows instantly."""
        self._show_section((self._section_index + 1) % len(self.SECTIONS))

    def action_previous_section(self) -> None:
        """Left/`h`: the previous tab, wrapping; the list follows instantly."""
        self._show_section(
            (self._section_index - 1) % len(self.SECTIONS)
        )

    def _activate_highlighted(self) -> None:
        section_value = self.SECTIONS[self._section_index][0]
        rows = self.items_for(section_value)
        items = self.query_one("#settings-items", OptionList)
        index = items.highlighted
        if index is None or not 0 <= index < len(rows):
            return
        self.run_worker(self._activate(rows[index].value))

    async def _activate(self, value: str) -> None:
        handler = self._on_action
        if handler is None:
            return
        outcome = handler(value)
        if hasattr(outcome, "__await__"):
            await outcome
        # The action may have flipped a pref or opened a sub-picker over us;
        # repaint the live values underneath either way.
        self._show_section(self._section_index)

    def action_close(self) -> None:
        self.dismiss(None)


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
