"""Concrete pickers built on the shared palette skeleton."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Generic, TypeVar

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static

from zenagent.app.models import ModelEntry
from zenagent.app.safety import Verdict
from zenagent.app.sessions import SessionEntry
from zenagent.cli.tui.screens.palette import PaletteItem, PaletteScreen
from zenagent.cli.tui.textutil import clip_cells
from zenagent.i18n import t
from zenagent.tools.fs import unified_diff

__all__ = [
    "AskQuestion",
    "ChoicePicker",
    "Confirm",
    "DialogChoice",
    "DialogScreen",
    "HistorySearch",
    "ModelPicker",
    "PlanReview",
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
                note=t(
                    "tui.picker.model_note",
                    context=entry.context_window // 1000,
                    state=(
                        t("tui.picker.ready")
                        if entry.available
                        else entry.reason
                    ),
                ),
                enabled=entry.available,
                selected=entry.current,
            )
            for entry in entries
        ]
        super().__init__(
            title=t("tui.picker.models"),
            items=items,
            placeholder=t("tui.picker.models_search"),
        )


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
                project_label = entry.project or t("tui.picker.legacy_project")
                label = f"{marker} [{project_label}] {entry.title or entry.path.name}"
            else:
                label = f"{marker} {entry.title or entry.path.name}"
            items.append(
                PaletteItem(
                    label=label,
                    value=SessionAction("resume", entry.path),
                    note=t(
                        "tui.picker.messages_note",
                        n=entry.messages,
                        time=time.strftime(
                            "%m-%d %H:%M", time.localtime(entry.created)
                        ),
                    ),
                    selected=entry.current,
                )
            )
        scope_hint = t(
            "tui.picker.scope_all" if scope == "all" else "tui.picker.scope_project"
        )
        super().__init__(
            title=t("tui.picker.sessions"),
            items=items,
            placeholder=t("tui.picker.sessions_search"),
            footer=t("tui.picker.session_footer", scope_hint=scope_hint),
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
        background: $ink 65%;
    }

    DialogScreen #dialog {
        width: 90%;
        max-width: 88;
        height: auto;
        padding: 1 2;
        background: $surface;
        border: round $line;
    }

    DialogScreen #dialog-title {
        height: auto;
        padding: 0 0 1 0;
        color: $accent;
        text-style: bold;
    }

    DialogScreen #dialog-body {
        height: auto;
        margin: 0 0 1 0;
    }

    DialogScreen .dialog-detail {
        height: auto;
        color: $muted;
    }

    DialogScreen .dialog-command,
    DialogScreen .dialog-target {
        height: auto;
        color: $foreground;
    }

    DialogScreen .dialog-note {
        height: auto;
        color: $dim;
    }

    DialogScreen .dialog-add {
        height: auto;
        color: $success;
    }

    DialogScreen .dialog-remove {
        height: auto;
        color: $error;
    }

    DialogScreen #dialog-risk {
        height: auto;
        padding: 0 0 1 0;
        color: $warning;
        text-style: bold;
    }

    DialogScreen #dialog-diff {
        height: auto;
        margin: 0 0 1 0;
    }

    DialogScreen #dialog-choices {
        height: auto;
    }

    DialogScreen .dialog-choice {
        height: 1;
        color: $muted;
    }

    DialogScreen .dialog-choice:hover {
        background: $raised;
        color: $foreground;
    }

    DialogScreen .dialog-choice.-primary {
        color: $accent;
    }

    DialogScreen #dialog-hint {
        height: auto;
        margin: 1 0 0 0;
        color: $dim;
    }

    DialogScreen #rename-input {
        height: 1;
        padding: 0;
        border: none;
        background: $ink;
        color: $foreground;
    }
    """


class DialogChoice(Static):
    """One clickable option row; the screen's ``action_<name>`` handles it.

    ``on_select`` is for dynamic option lists (the ask dialog): when provided it
    runs on click instead of looking up ``action_<action>`` by name, so a screen
    can build rows from model-provided labels without one action method per row.
    """

    def __init__(
        self,
        text: Text,
        action: str,
        *,
        primary: bool = False,
        on_select: Callable[[], None] | None = None,
    ) -> None:
        self.action_name = action
        self._text = text
        self._on_select = on_select
        classes = "dialog-choice" + (" -primary" if primary else "")
        super().__init__(text, classes=classes)

    @property
    def plain(self) -> str:
        return self._text.plain

    def set_text(self, text: Text) -> None:
        """Replace the row label (multi-select toggles redraw their marker)."""
        self._text = text
        self.update(text)

    def on_click(self) -> None:
        if self._on_select is not None:
            self._on_select()
            return
        handler = getattr(self.screen, f"action_{self.action_name}", None)
        if handler is not None:
            handler()


def _choice(key: str, label: str) -> Text:
    """``[y] allow`` -- Text, so the square brackets stay literal markup."""
    return Text(t("tui.dialog.key_hint", key=key, label=label))


def _clip(text: str, limit: int) -> str:
    flat = " ".join(text.split())
    return clip_cells(flat, limit)


#: Above this size a diff preview is noise (and reading it would stall the UI).
_MAX_PREVIEW_BYTES = 256 * 1024


def preview_diff(
    tool_name: str, arguments: dict, *, max_lines: int = 12
) -> list[str]:
    """Bounded unified-diff preview for a write/edit approval, or ``[]``.

    Reads the target itself (the tool has not run yet); a missing or binary
    file, a non-matching edit, or any other tool yields no preview rather than
    a misleading one.
    """
    raw_path = arguments.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        return []
    target = Path(raw_path).expanduser()
    try:
        if not target.is_file():
            return []
        if target.stat().st_size > _MAX_PREVIEW_BYTES:
            return []
        data = target.read_bytes()
    except OSError:
        return []
    if b"\x00" in data[:8192]:
        return []
    before = data.decode("utf-8", errors="replace")
    if tool_name == "write_file":
        content = arguments.get("content")
        if not isinstance(content, str):
            return []
        after = content
    elif tool_name == "edit_file":
        old = arguments.get("old_string")
        new = arguments.get("new_string")
        if not isinstance(old, str) or not isinstance(new, str) or not old:
            return []
        if old not in before:
            return []
        after = before.replace(old, new, 1)
    else:
        return []
    lines = unified_diff(str(target), before, after).splitlines()
    if not lines:
        return []
    shown = lines[:max_lines]
    if len(lines) > max_lines:
        shown.append("…")
    return shown


def _approval_body(tool_name: str, arguments: dict) -> list[tuple[str, str]]:
    """``(text, css_class)`` rows: what the model wants to run, at a glance."""
    rows: list[tuple[str, str]] = []

    command = arguments.get("command")
    if isinstance(command, str) and command.strip():
        lines = command.rstrip().splitlines()
        rows.append((f"$ {lines[0]}", "dialog-command"))
        rows.extend((f"  {line}", "dialog-command") for line in lines[1:8])
        if len(lines) > 8:
            rows.append(
                (t("tui.dialog.more_lines", n=len(lines) - 8), "dialog-note")
            )

    path = arguments.get("path")
    if isinstance(path, str) and path:
        rows.append((path, "dialog-target"))

    content = arguments.get("content")
    if isinstance(content, str) and content:
        rows.append(
            (
                t("tui.dialog.lines_to_write", n=len(content.splitlines())),
                "dialog-note",
            )
        )

    old = arguments.get("old_string")
    new = arguments.get("new_string")
    if isinstance(old, str) and old:
        rows.append((f"- {_clip(old, 100)}", "dialog-remove"))
    if isinstance(new, str) and new:
        rows.append((f"+ {_clip(new, 100)}", "dialog-add"))

    pattern = arguments.get("pattern")
    if isinstance(pattern, str) and pattern:
        rows.append((t("tui.dialog.pattern", pattern=pattern), "dialog-detail"))

    if not rows:
        for key, value in list(arguments.items())[:5]:
            rows.append((f"{key} = {_clip(str(value), 100)}", "dialog-detail"))
        if len(arguments) > 5:
            rows.append(
                (
                    t("tui.dialog.more_arguments", n=len(arguments) - 5),
                    "dialog-note",
                )
            )
    return rows


class Confirm(DialogScreen[bool]):
    """Yes/no confirmation for destructive actions."""

    BINDINGS = [("y", "yes", "Yes"), ("n,escape", "no", "No")]  # noqa: RUF012

    def __init__(self, prompt: str) -> None:
        super().__init__()
        self.prompt = prompt

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(Text(t("tui.dialog.confirm")), id="dialog-title")
            with Vertical(id="dialog-body"):
                yield Static(Text(self.prompt), classes="dialog-detail")
            with Vertical(id="dialog-choices"):
                yield DialogChoice(
                    _choice("y", t("tui.dialog.yes")), "yes", primary=True
                )
                yield DialogChoice(_choice("n", t("tui.dialog.no")), "no")

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)


class PlanReview(DialogScreen[str]):
    """Read-only review of the last plan; approving switches to full mode."""

    BINDINGS = [  # noqa: RUF012 - Textual class API
        ("a", "approve", "Approve"),
        ("k,escape", "keep", "Keep plan"),
    ]

    def __init__(self, plan: str) -> None:
        super().__init__()
        self.plan = plan

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(Text(t("tui.dialog.plan_review")), id="dialog-title")
            with VerticalScroll(id="dialog-body"):
                yield Static(Text(self.plan), classes="dialog-detail")
            with Vertical(id="dialog-choices"):
                yield DialogChoice(
                    _choice("a", t("tui.dialog.approve_full")), "approve", primary=True
                )
                yield DialogChoice(_choice("k", t("tui.dialog.keep_plan")), "keep")

    def action_approve(self) -> None:
        self.dismiss("approve")

    def action_keep(self) -> None:
        self.dismiss("keep")


class RenamePrompt(DialogScreen[str]):
    """A one-line rename dialog for a saved session."""

    BINDINGS = [("escape", "cancel", "Cancel")]  # noqa: RUF012 - Textual class API

    def __init__(self, value: str = "") -> None:
        super().__init__()
        self.initial = value

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(Text(t("tui.dialog.rename")), id="dialog-title")
            with Vertical(id="dialog-body"):
                yield Input(
                    value=self.initial,
                    id="rename-input",
                    placeholder=t("tui.dialog.rename_placeholder"),
                )
            yield Static(Text(t("tui.dialog.save_cancel")), id="dialog-hint")

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
        verdict: Verdict | None = None,
    ) -> None:
        super().__init__()
        self.tool_name = tool_name
        self.arguments = arguments
        self.timeout = timeout
        #: The exact scope an "always" answer would remember (path / command).
        self.target = target
        #: Confirm-level risk from the broker (outside/protected/destructive).
        self.verdict = verdict

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(
                Text(t("tui.dialog.allow", tool=self.tool_name)),
                id="dialog-title",
            )
            risk = self._risk_text()
            if risk:
                yield Static(Text(risk), id="dialog-risk")
            body = _approval_body(self.tool_name, self.arguments)
            if body:
                with Vertical(id="dialog-body"):
                    for line, css_class in body:
                        yield Static(Text(line), classes=css_class)
            diff = preview_diff(self.tool_name, self.arguments)
            if diff:
                with Vertical(id="dialog-diff"):
                    for line in diff:
                        if line.startswith("+"):
                            css_class = "dialog-add"
                        elif line.startswith("-"):
                            css_class = "dialog-remove"
                        else:
                            css_class = "dialog-detail"
                        yield Static(Text(line), classes=css_class)
            if self.target:
                yield Static(
                    Text(t("tui.dialog.always_allows", target=self.target)),
                    id="dialog-scope",
                )
            with Vertical(id="dialog-choices"):
                yield DialogChoice(
                    _choice("y", t("tui.dialog.allow_choice")), "allow", primary=True
                )
                yield DialogChoice(
                    _choice("a", t("tui.dialog.allow_session")), "always"
                )
                yield DialogChoice(_choice("n", t("tui.dialog.deny")), "deny")
            if self.timeout:
                yield Static(
                    Text(
                        t("tui.dialog.timeout", seconds=f"{self.timeout:g}")
                    ),
                    id="dialog-hint",
                )

    def _risk_text(self) -> str:
        verdict = self.verdict
        if verdict is None or verdict.risk is None:
            return ""
        if verdict.risk == "destructive":
            return t("tui.dialog.risk_destructive", reason=verdict.reason)
        target = self.arguments.get("path") or verdict.target or ""
        return t(f"tui.dialog.risk_{verdict.risk}", target=target)

    def action_allow(self) -> None:
        self.dismiss("allow")

    def action_always(self) -> None:
        self.dismiss("always")

    def action_deny(self) -> None:
        self.dismiss("deny")


class AskQuestion(DialogScreen[list[str] | None]):
    """One question from the ``ask`` tool: pick one option, or several when multi.

    Single-select questions dismiss on choice (``1``-``6`` or a click); ``multi``
    questions toggle rows and submit on Enter, where an empty selection is a
    valid "none of these" answer. Escape dismisses the whole ask (``None``),
    which the caller reports as unanswered; a stray Enter never submits a
    single-select answer, mirroring :class:`ToolApproval`.
    """

    BINDINGS = [  # noqa: RUF012 - Textual class API
        ("escape", "cancel", "Cancel"),
        ("enter", "submit", "Submit"),
        ("1", "choose_1", "1"),
        ("2", "choose_2", "2"),
        ("3", "choose_3", "3"),
        ("4", "choose_4", "4"),
        ("5", "choose_5", "5"),
        ("6", "choose_6", "6"),
    ]

    def __init__(
        self, question: dict, *, index: int = 1, total: int = 1
    ) -> None:
        super().__init__()
        self.question = question
        self.index = index
        self.total = total
        self.selected: list[str] = []
        self._rows: list[DialogChoice] = []

    @property
    def multi(self) -> bool:
        return bool(self.question.get("multi"))

    def compose(self) -> ComposeResult:
        title = self.question.get("header") or t(
            "tui.dialog.question", index=self.index, total=self.total
        )
        if self.total > 1 and self.question.get("header"):
            title = f"{title} · {self.index}/{self.total}"
        with Vertical(id="dialog"):
            yield Static(Text(title), id="dialog-title")
            with Vertical(id="dialog-body"):
                yield Static(
                    Text(self.question["question"]), classes="dialog-detail"
                )
                recommended = self.question.get("recommended")
                if recommended:
                    yield Static(
                        Text(t("tui.dialog.recommended", label=recommended)),
                        classes="dialog-note",
                    )
            with Vertical(id="dialog-choices"):
                for position, option in enumerate(self.question["options"]):
                    row = DialogChoice(
                        self._choice_text(position, option),
                        f"option_{position}",
                        primary=option["label"] == self.question.get("recommended"),
                        on_select=lambda position=position: self._choose(position),
                    )
                    self._rows.append(row)
                    yield row
            yield Static(Text(self._hint()), id="dialog-hint")

    def _hint(self) -> str:
        if self.multi:
            return t("tui.dialog.multi_hint")
        return t("tui.dialog.single_hint")

    def _choice_text(self, position: int, option: dict) -> Text:
        text = Text(f"[{position + 1}] ")
        if self.multi:
            text.append("✓ " if option["label"] in self.selected else "  ")
        text.append(option["label"])
        if option["label"] == self.question.get("recommended"):
            text.append(t("tui.dialog.recommended_suffix"))
        description = option.get("description")
        if description:
            text.append(f"  — {description}")
        return text

    def _refresh_rows(self) -> None:
        for position, row in enumerate(self._rows):
            row.set_text(
                self._choice_text(position, self.question["options"][position])
            )

    def _choose(self, position: int) -> None:
        options = self.question["options"]
        if not 0 <= position < len(options):
            return
        label = options[position]["label"]
        if not self.multi:
            self.dismiss([label])
            return
        if label in self.selected:
            self.selected.remove(label)
        else:
            self.selected.append(label)
        self._refresh_rows()

    def action_choose_1(self) -> None:
        self._choose(0)

    def action_choose_2(self) -> None:
        self._choose(1)

    def action_choose_3(self) -> None:
        self._choose(2)

    def action_choose_4(self) -> None:
        self._choose(3)

    def action_choose_5(self) -> None:
        self._choose(4)

    def action_choose_6(self) -> None:
        self._choose(5)

    def action_submit(self) -> None:
        if self.multi:
            self.dismiss(list(self.selected))

    def action_cancel(self) -> None:
        self.dismiss(None)


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
