"""Secrets manager modal: the TUI's ``/secrets`` panel.

One row per provider (model catalog first, then the search chain), with set /
clear / test on the highlighted row — the same surface the desktop's secrets
settings exposes, backed by the same store (:mod:`zenagent.settings`, 0600).
Every action re-reads the store: keys can change underneath an open panel.
Key values are never displayed; ``configured`` is all this panel knows.
"""

from __future__ import annotations

from dataclasses import dataclass

from textual import on
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from zenagent.cli.tui.screens.modals import Confirm, DialogScreen
from zenagent.cli.tui.screens.secrets import SecretInput
from zenagent.i18n import t

__all__ = ["SecretsManager"]


@dataclass(frozen=True)
class SecretRow:
    """One provider row behind an option; ``scope`` routes test and set."""

    scope: str  # "model" | "search"
    id: str
    label: str
    env: str
    configured: bool
    current: bool = False
    models: int = 0


class SecretsManager(DialogScreen[None]):
    """Provider key panel: list + s/x/t; every action re-reads the store."""

    BINDINGS = [  # noqa: RUF012 - Textual class API
        ("s", "set", "Set"),
        ("x", "clear", "Clear"),
        ("t", "test", "Test"),
        ("escape", "close", "Close"),
    ]

    def __init__(self, runtime, *, preselect: str = "") -> None:
        super().__init__()
        self.runtime = runtime
        self.preselect = preselect
        #: Rows behind the option lines; ``_row_at`` maps option → row.
        self._rows: list[SecretRow] = []
        self._row_at: list[int | None] = []

    # -- data ---------------------------------------------------------------

    def _collect(self) -> list[SecretRow]:
        from zenagent import settings
        from zenagent.tools.web.providers import KEY_ENVS, LABELS

        current = getattr(getattr(self.runtime, "model", None), "provider", None)
        rows: list[SecretRow] = []
        for provider in sorted(
            self.runtime.catalog.values(), key=lambda item: item.name.lower()
        ):
            rows.append(
                SecretRow(
                    scope="model",
                    id=provider.id,
                    label=provider.name,
                    env=provider.api_key_env,
                    configured=bool(settings.get_api_key(provider.api_key_env)),
                    current=provider.id == current,
                    models=len(provider.models),
                )
            )
        for provider_id, env in KEY_ENVS.items():
            rows.append(
                SecretRow(
                    scope="search",
                    id=provider_id,
                    label=LABELS.get(provider_id, provider_id),
                    env=env,
                    configured=bool(settings.get_api_key(env)),
                )
            )
        return rows

    # -- layout -------------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(t("tui.secrets.manager_title"), id="dialog-title")
            with Vertical(id="dialog-body"):
                yield OptionList(id="secrets-list")
            yield Static(t("tui.secrets.manager_hint"), id="dialog-hint")

    def on_mount(self) -> None:
        self._refresh()
        self.query_one("#secrets-list", OptionList).focus()

    def _refresh(self) -> None:
        option_list = self.query_one("#secrets-list", OptionList)
        previous = self._selected_row_index(option_list)
        option_list.clear_options()
        self._rows = self._collect()
        #: Option index → row index; None for the section headers.
        self._row_at.clear()
        if not self._rows:
            self._row_at.append(None)
            option_list.add_option(Option(t("tui.secrets.empty"), disabled=True))
            return
        scope = ""
        for index, row in enumerate(self._rows):
            if row.scope != scope:
                scope = row.scope
                self._row_at.append(None)
                option_list.add_option(
                    Option(
                        t(
                            "tui.secrets.section_model"
                            if scope == "model"
                            else "tui.secrets.section_search"
                        ),
                        disabled=True,
                    )
                )
            status = (
                t("tui.secrets.key_set")
                if row.configured
                else t("tui.secrets.needs", env=row.env)
            )
            marker = "●" if row.configured else " "
            note = t("tui.secrets.current") if row.current else ""
            if row.scope == "model":
                text = t(
                    "tui.secrets.row_model",
                    marker=marker,
                    name=row.label,
                    env=row.env,
                    status=status,
                    count=row.models,
                    note=note,
                )
            else:
                text = t(
                    "tui.secrets.row_search",
                    marker=marker,
                    name=row.label,
                    env=row.env,
                    status=status,
                )
            self._row_at.append(index)
            option_list.add_option(Option(text))
        self._restore_highlight(option_list, previous)
        self.query_one("#dialog-title", Static).update(
            t(
                "tui.secrets.manager_count",
                configured=sum(row.configured for row in self._rows),
                total=len(self._rows),
            )
        )

    def _selected_row_index(self, option_list: OptionList) -> int | None:
        """The row behind the highlighted option (headers map to None)."""
        index = option_list.highlighted
        if index is None or not 0 <= index < len(self._row_at):
            return None
        return self._row_at[index]

    def _option_for_row(self, row_index: int) -> int | None:
        """The option index a row index lives at (headers shift the list)."""
        seen = -1
        for option_index, row_index_at in enumerate(self._row_at):
            if row_index_at is None:
                continue
            seen += 1
            if seen == row_index:
                return option_index
        return None

    def _restore_highlight(self, option_list: OptionList, previous: int | None) -> None:
        """Keep the selection stable across refreshes; honour a preselect once."""
        target: int | None = None
        if self.preselect:
            for index, row in enumerate(self._rows):
                if row.env == self.preselect or row.id == self.preselect:
                    target = index
                    self.preselect = ""
                    break
        if target is None and previous is not None:
            target = previous
        if target is not None:
            option = self._option_for_row(target)
            if option is not None:
                option_list.highlighted = option
                return
        if option_list.highlighted is None:
            option_list.highlighted = self._option_for_row(0)
            option_list.highlighted = previous
        elif option_list.highlighted is None:
            option_list.highlighted = 0

    def _selected(self) -> SecretRow | None:
        option_list = self.query_one("#secrets-list", OptionList)
        row_index = self._selected_row_index(option_list)
        if row_index is None:
            return None
        return self._rows[row_index]

    # -- actions ------------------------------------------------------------

    @on(OptionList.OptionSelected)
    def _open_selected(self, _event: OptionList.OptionSelected) -> None:
        self.action_set()

    def action_set(self) -> None:
        row = self._selected()
        if row is None:
            return
        self.app.push_screen(
            SecretInput(
                title=row.env,
                hint=t(
                    "tui.secrets.hint",
                    scope=t(
                        "tui.secrets.scope_model"
                        if row.scope == "model"
                        else "tui.secrets.scope_search"
                    ),
                ),
            ),
            self._entered,
        )

    def _entered(self, value: str | None) -> None:
        if not value:
            return
        row = self._selected()
        if row is None:
            return
        self._save(row, value)

    def action_clear(self) -> None:
        row = self._selected()
        if row is None:
            return
        if not row.configured:
            self.notify(t("tui.secrets.not_set", env=row.env), timeout=3)
            return

        def cleared(ok: bool | None) -> None:
            if ok:
                self._save(row, "")

        self.app.push_screen(
            Confirm(t("tui.secrets.clear_confirm", env=row.env)), cleared
        )

    def _save(self, row: SecretRow, value: str) -> None:
        from typing import cast

        from zenagent.cli.tui.app_parts.models import ModelsMixin

        app = cast("ModelsMixin", self.app)
        recovered = app._apply_secret(row.env, value)
        if value:
            self.notify(
                t(
                    "tui.app.saved_key_ready" if recovered else "tui.app.saved_key",
                    env=row.env,
                ),
                timeout=3,
            )
        else:
            self.notify(t("tui.secrets.cleared", env=row.env), timeout=3)
        self._refresh()

    def action_test(self) -> None:
        row = self._selected()
        if row is None:
            return
        self.run_worker(self._probe(row))

    async def _probe(self, row: SecretRow) -> None:
        from zenagent.app import probes

        if row.scope == "model":
            result = await probes.probe_model_provider(self.runtime.catalog, row.id)
        else:
            result = await probes.probe_search_provider(row.id)
        if result.connected:
            self.notify(t("tui.secrets.test_ok", name=row.label), timeout=4)
        else:
            self.notify(
                t("tui.secrets.test_fail", name=row.label, message=result.message),
                timeout=6,
            )

    def action_close(self) -> None:
        self.dismiss(None)
