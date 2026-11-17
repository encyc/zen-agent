"""Limits manager modal: the TUI's ``/limits`` panel.

The same registry-driven surface as the desktop's budget page: every knob in
:data:`zenagent.limits.ALL_KNOBS`, edited through the managed config tables
and adopted live through :func:`zenagent.app.limits_apply.reload_into` — the
identical write path the wire ``limits`` op uses, so TUI and GUI cannot
drift. Rows marked ``*`` are beaten by this session's CLI switches (the file
change still lands for future sessions).
"""

from __future__ import annotations

from dataclasses import dataclass

from textual import on
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from zenagent.cli.tui.screens.modals import Confirm, DialogScreen
from zenagent.i18n import t

__all__ = ["LimitsManager", "ValueInput"]


class ValueInput(DialogScreen[str | None]):
    """One plain-text prompt for a knob value; Enter saves, Esc cancels."""

    BINDINGS = [  # noqa: RUF012 - Textual class API
        ("escape", "cancel", "Cancel"),
    ]

    DEFAULT_CSS = """
    ValueInput Input {
        height: 1;
        border: none;
        background: $ink;
        color: $foreground;
    }
    """

    def __init__(self, *, title: str, hint: str, current: str) -> None:
        super().__init__()
        self.title_text = title
        self.hint_text = hint
        self.current = current

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(self.title_text, id="dialog-title")
            yield Input(value=self.current, id="value-input")
            yield Static(self.hint_text, id="dialog-hint")

    def on_mount(self) -> None:
        self.query_one("#value-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip())

    def action_cancel(self) -> None:
        self.dismiss(None)


@dataclass(frozen=True)
class LimitRow:
    """One knob behind an option line."""

    key: str
    attr: str
    value: object
    explicit: bool
    override: str | None


class LimitsManager(DialogScreen[None]):
    """Budget knobs: enter edits, r resets to the config default."""

    BINDINGS = [  # noqa: RUF012 - Textual class API
        ("enter", "edit", "Edit"),
        ("r", "reset", "Reset"),
        ("escape", "close", "Close"),
    ]

    def __init__(self, runtime) -> None:
        super().__init__()
        self.runtime = runtime
        #: Knobs behind the option lines; ``_row_at`` maps option → knob.
        self._rows: list[LimitRow] = []
        self._row_at: list[int | None] = []

    # -- data ---------------------------------------------------------------

    def _collect(self) -> list[LimitRow]:
        """Effective values, exactly as the wire ``limits get`` reports them."""
        from zenagent.app.limits_apply import overrides
        from zenagent.config import config_path
        from zenagent.limits import ALL_KNOBS, read_managed

        explicit = {
            f"{table}.{name}"
            for table, rows in read_managed(config_path()).items()
            for name in rows
        }
        knob_overrides = overrides(self.runtime)
        rows = []
        for knob in ALL_KNOBS:
            rows.append(
                LimitRow(
                    key=knob.key,
                    attr=knob.attr,
                    value=self._knob_value(knob),
                    explicit=knob.key in explicit,
                    override=knob_overrides.get(knob.attr),
                )
            )
        return rows

    def _knob_value(self, knob) -> object:
        """What this build of the session runs with (file beats stale memory)."""
        from zenagent.config import config_path, load_user_config

        config = getattr(self.runtime, "user_config", None)
        if config is not None and knob.table.startswith("limits"):
            return config.limits.value(knob.attr)
        return getattr(load_user_config(config_path()), knob.attr)

    # -- layout -------------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(t("tui.limits.manager_title"), id="dialog-title")
            with Vertical(id="dialog-body"):
                yield OptionList(id="limits-list")
            yield Static(t("tui.limits.manager_hint"), id="dialog-hint")

    def on_mount(self) -> None:
        self._refresh()
        self.query_one("#limits-list", OptionList).focus()

    def _refresh(self) -> None:
        option_list = self.query_one("#limits-list", OptionList)
        previous = self._selected_row_index(option_list)
        option_list.clear_options()
        self._rows = self._collect()
        self._row_at.clear()
        section = ""
        for index, row in enumerate(self._rows):
            section_key = row.key.rsplit(".", 1)[0]
            if section_key != section:
                section = section_key
                self._row_at.append(None)
                option_list.add_option(Option(section, disabled=True))
            if row.override:
                marker = "*"
                note = t("tui.limits.overridden", switch=row.override)
            elif row.explicit:
                marker = "●"
                note = ""
            else:
                marker = " "
                note = ""
            self._row_at.append(index)
            option_list.add_option(
                Option(t("tui.limits.row", marker=marker, key=row.key, value=row.value, note=note))
            )
        self._restore_highlight(option_list, previous)
        self.query_one("#dialog-title", Static).update(
            t("tui.limits.manager_count", count=len(self._rows))
        )

    def _selected_row_index(self, option_list: OptionList) -> int | None:
        """The knob behind the highlighted option (headers map to None)."""
        index = option_list.highlighted
        if index is None or not 0 <= index < len(self._row_at):
            return None
        return self._row_at[index]

    def _option_for_row(self, row_index: int) -> int | None:
        """The option index a knob index lives at (headers shift the list)."""
        seen = -1
        for option_index, row_index_at in enumerate(self._row_at):
            if row_index_at is None:
                continue
            seen += 1
            if seen == row_index:
                return option_index
        return None

    def _restore_highlight(self, option_list: OptionList, previous: int | None) -> None:
        if previous is not None:
            option = self._option_for_row(previous)
            if option is not None:
                option_list.highlighted = option
                return
        if option_list.highlighted is None:
            option_list.highlighted = self._option_for_row(0)

    def _selected(self) -> LimitRow | None:
        option_list = self.query_one("#limits-list", OptionList)
        row_index = self._selected_row_index(option_list)
        if row_index is None:
            return None
        return self._rows[row_index]

    # -- actions ------------------------------------------------------------

    @on(OptionList.OptionSelected)
    def _open_selected(self, _event: OptionList.OptionSelected) -> None:
        self.action_edit()

    def action_edit(self) -> None:
        row = self._selected()
        if row is None:
            return
        from zenagent.limits import KNOB_BY_KEY

        knob = KNOB_BY_KEY[row.key]
        hint = t(
            "tui.limits.edit_hint",
            minimum=knob.minimum,
            maximum=knob.maximum,
            default=knob.default,
        )
        if knob.choices:
            hint = t("tui.limits.edit_choices", choices=", ".join(knob.choices))
        elif knob.nullable:
            hint = t("tui.limits.edit_nullable", hint=hint)
        self.app.push_screen(
            ValueInput(title=row.key, hint=hint, current=str(row.value)),
            self._save,
        )

    def _save(self, text: str | None) -> None:
        if text is None:
            return
        row = self._selected()
        if row is None:
            return
        from zenagent.config import config_path
        from zenagent.limits import KNOB_BY_KEY, coerce_value

        knob = KNOB_BY_KEY[row.key]
        try:
            value: object
            if text == "" and knob.nullable:
                value = None  # the "off" spelling the desktop page also uses
            elif knob.kind == "int":
                value = int(text)
            elif knob.kind == "float":
                value = float(text)
            else:
                value = text
            self._write(knob, coerce_value(knob, value, config_path()))
        except (ValueError, OSError) as exc:
            self.notify(t("tui.limits.invalid", error=exc), timeout=6)

    def action_reset(self) -> None:
        row = self._selected()
        if row is None:
            return
        if not row.explicit:
            self.notify(t("tui.limits.not_explicit", key=row.key), timeout=3)
            return
        from zenagent.limits import KNOB_BY_KEY

        knob = KNOB_BY_KEY[row.key]

        def reset(ok: bool | None) -> None:
            if ok:
                self._write(knob, None, reset=True)

        self.app.push_screen(
            Confirm(t("tui.limits.reset_confirm", key=row.key)), reset
        )

    def _write(self, knob, value, *, reset: bool = False) -> None:
        from zenagent.config import config_path
        from zenagent.limits import LimitsWriteError, read_managed, write_managed

        try:
            # Always start from the file: the panel may hold a stale snapshot.
            tables = read_managed(config_path())
            rows = tables.setdefault(knob.table, {})
            if reset:
                rows.pop(knob.name, None)
            else:
                rows[knob.name] = value
            if not rows:
                tables.pop(knob.table, None)
            write_managed(config_path(), tables)
        except LimitsWriteError as exc:
            self.notify(t("tui.limits.invalid", error=exc), timeout=6)
            return
        from zenagent.app.limits_apply import reload_into

        reload_into(self.runtime)
        self.runtime.apply_limits()
        self.notify(
            t("tui.limits.saved_reset" if reset else "tui.limits.saved", key=knob.key),
            timeout=3,
        )
        self._refresh()

    def action_close(self) -> None:
        self.dismiss(None)
