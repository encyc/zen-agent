"""Approval and prompt dialogs (moved from modals.py, audit P2)."""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Input, Static

from zenagent.app.safety import Verdict
from zenagent.i18n import t
from zenagent.cli.tui.screens.dialogs import DialogChoice, DialogScreen, preview_diff, _choice, _clip


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
    """Read-only review of the last plan; approving switches to auto mode."""

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
                    _choice("a", t("tui.dialog.approve_auto")), "approve", primary=True
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


