"""Runs ledger modal: the TUI's ``/runs`` panel.

Read-only over :class:`zenagent.app.runs.RunLedger` — the first frontend to
surface the run ledger the wire ``runs`` op already exposes; the desktop can
adopt the same information architecture later. Enter opens one run's detail;
nothing here mutates the ledger.
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from zenagent.app.runs import RunRecord, clock_text, duration_text, status_marks
from zenagent.cli.tui.screens.dialogs import DialogScreen
from zenagent.i18n import t

__all__ = ["RunsScreen", "RunDetail"]


class RunsScreen(DialogScreen[None]):
    """The project's run ledger, newest first; enter shows one run."""

    BINDINGS = [  # noqa: RUF012 - Textual class API
        ("escape", "close", "Close"),
    ]

    def __init__(self, runtime, *, limit: int = 100) -> None:
        super().__init__()
        self.runtime = runtime
        self.limit = limit
        #: Records behind the option lines; ``_row_at`` maps option → record.
        self._rows: list[RunRecord] = []
        self._row_at: list[int | None] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(t("tui.runs.title"), id="dialog-title")
            with Vertical(id="dialog-body"):
                yield OptionList(id="runs-list")
            yield Static(t("tui.runs.hint"), id="dialog-hint")

    def on_mount(self) -> None:
        self._refresh()
        self.query_one("#runs-list", OptionList).focus()

    def _ledger(self):
        return getattr(self.runtime, "run_ledger", None)

    def _refresh(self) -> None:
        option_list = self.query_one("#runs-list", OptionList)
        option_list.clear_options()
        self._rows = []
        self._row_at.clear()
        ledger = self._ledger()
        if ledger is None:
            self._row_at.append(None)
            option_list.add_option(Option(t("tui.runs.empty"), disabled=True))
        else:
            self._rows = ledger.list(limit=self.limit)
            if not self._rows:
                self._row_at.append(None)
                option_list.add_option(Option(t("tui.runs.empty"), disabled=True))
            for index, record in enumerate(self._rows):
                self._row_at.append(index)
                option_list.add_option(
                    Option(
                        t(
                            "tui.runs.row",
                            mark=status_marks.get(record.status, "?"),
                            clock=clock_text(record.started_at),
                            kind=record.kind,
                            label=record.label or record.agent_id or record.id,
                            status=record.status,
                            duration=duration_text(record.duration_ms),
                            cost=record.cost,
                        )
                    )
                )
            for warning in ledger.warnings:
                self._row_at.append(None)
                option_list.add_option(
                    Option(t("tui.runs.warning", warning=warning), disabled=True)
                )
        self.query_one("#dialog-title", Static).update(
            t("tui.runs.count", count=len(self._rows))
        )

    def _selected(self) -> RunRecord | None:
        option_list = self.query_one("#runs-list", OptionList)
        index = option_list.highlighted
        if index is None or not 0 <= index < len(self._row_at):
            return None
        row_index = self._row_at[index]
        if row_index is None:
            return None
        return self._rows[row_index]

    @on(OptionList.OptionSelected)
    def _open_selected(self, _event: OptionList.OptionSelected) -> None:
        record = self._selected()
        if record is not None:
            self.app.push_screen(RunDetail(record))

    def action_close(self) -> None:
        self.dismiss(None)


class RunDetail(DialogScreen[None]):
    """One ledger record, field by field; escape returns to the list."""

    BINDINGS = [("escape", "close", "Close")]  # noqa: RUF012 - Textual class API

    #: The record fields worth showing, in order (raw names: they are the
    #: ledger's vocabulary, shared with the wire ``runs`` op).
    _FIELDS = (
        "kind",
        "status",
        "agent_id",
        "session",
        "work_item_id",
        "task_id",
        "label",
        "termination",
        "turns",
        "cost",
        "started_at",
        "ended_at",
        "duration_ms",
        "error",
    )

    def __init__(self, record: RunRecord) -> None:
        super().__init__()
        self.record = record
        #: The composed field text; tests read this instead of Static internals.
        self.text = ""

    def compose(self) -> ComposeResult:
        lines = [t("tui.runs.detail_title", id=self.record.id)]
        for field in self._FIELDS:
            value = getattr(self.record, field)
            if value in (None, "", 0.0):
                continue
            if field in ("started_at", "ended_at"):
                value = clock_text(value)
            elif field == "duration_ms":
                value = duration_text(value)
            lines.append(f"{field}: {value}")
        self.text = "\n".join(lines)
        with Vertical(id="dialog"):
            yield Static(self.text, classes="dialog-detail")
            yield Static(t("tui.runs.detail_hint"), id="dialog-hint")

    def action_close(self) -> None:
        self.dismiss(None)
