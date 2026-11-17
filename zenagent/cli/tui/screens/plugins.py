"""Plugins manager modal: the TUI's ``/plugins`` panel.

Same surface as the desktop's plugin settings: installed plugins with a
toggle, the builtin catalog with install. Writes go through
:mod:`zenagent.plugins` and then re-apply the live runtime — deferred while a
turn is streaming, the same contract the wire path (``ops_plugins``) uses.
Credentials are edited from the desktop or the config files; this panel only
reports whether any are missing.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from textual import on
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from zenagent.cli.tui.screens.modals import Confirm, DialogScreen
from zenagent.i18n import t

__all__ = ["PluginsManager"]


@dataclass(frozen=True)
class PluginRow:
    """One line behind an option; ``section`` routes toggle/install/uninstall."""

    section: str  # "installed" | "catalog"
    id: str
    label: str
    enabled: bool = False
    status_key: str = ""
    status_args: dict = None  # type: ignore[assignment]


def _status_key(enabled: bool, missing: int, states: set[str]) -> str:
    """The wire's ``_status`` ladder, one i18n key per rung."""
    if not enabled:
        return "tui.plugins.status_disabled"
    if missing:
        return "tui.plugins.status_needs_credentials"
    if "failed" in states:
        return "tui.plugins.status_failed"
    if "connected" in states:
        return "tui.plugins.status_connected"
    return "tui.plugins.status_ready"


class PluginsManager(DialogScreen[None]):
    """Installed plugins + builtin catalog; enter toggles, i installs."""

    BINDINGS = [  # noqa: RUF012 - Textual class API
        ("enter", "toggle", "Toggle"),
        ("i", "install", "Install"),
        ("x", "uninstall", "Uninstall"),
        ("escape", "close", "Close"),
    ]

    def __init__(self, runtime) -> None:
        super().__init__()
        self.runtime = runtime
        #: Rows behind the option lines; ``_row_at`` maps option → row.
        self._rows: list[PluginRow] = []
        self._row_at: list[int | None] = []
        self._warnings: list[str] = []

    # -- data ---------------------------------------------------------------

    def _collect(self) -> None:
        from zenagent.plugins.catalog import builtin_catalog
        from zenagent.plugins.discovery import discover_plugins
        from zenagent.plugins.state import credentials_for, is_enabled

        manifests, self._warnings = discover_plugins()
        statuses = {}
        mcp = getattr(self.runtime, "mcp", None)
        if mcp is not None:
            statuses = {item.name: item for item in mcp.status()}
        rows: list[PluginRow] = []
        for manifest in manifests:
            enabled = is_enabled(manifest.id)
            credentials = credentials_for(manifest.id)
            missing = sum(
                1 for spec in manifest.credentials if not credentials.get(spec.key)
            )
            states = {
                (
                    statuses[f"{manifest.id}.{key}"].state
                    if f"{manifest.id}.{key}" in statuses
                    else "off"
                )
                for key in manifest.mcp_servers
            }
            rows.append(
                PluginRow(
                    section="installed",
                    id=manifest.id,
                    label=f"{manifest.name} v{manifest.version}",
                    enabled=enabled,
                    status_key=_status_key(enabled, missing, states),
                    status_args={"missing": missing},
                )
            )
        installed = {manifest.id for manifest in manifests}
        for entry in builtin_catalog():
            rows.append(
                PluginRow(
                    section="catalog",
                    id=entry.id,
                    label=entry.name,
                    enabled=entry.id in installed,
                    status_key=(
                        "tui.plugins.status_installed"
                        if entry.id in installed
                        else "tui.plugins.status_installable"
                    ),
                )
            )
        self._rows = rows

    # -- layout -------------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(t("tui.plugins.manager_title"), id="dialog-title")
            with Vertical(id="dialog-body"):
                yield OptionList(id="plugins-list")
            yield Static(t("tui.plugins.manager_hint"), id="dialog-hint")

    def on_mount(self) -> None:
        self._refresh()
        self.query_one("#plugins-list", OptionList).focus()

    def _refresh(self) -> None:
        option_list = self.query_one("#plugins-list", OptionList)
        previous = self._selected_row_index(option_list)
        option_list.clear_options()
        self._collect()
        #: Option index → row index; None for headers and warnings.
        self._row_at.clear()
        section = ""
        for index, row in enumerate(self._rows):
            if row.section != section:
                section = row.section
                self._row_at.append(None)
                option_list.add_option(
                    Option(
                        t(
                            "tui.plugins.section_installed"
                            if section == "installed"
                            else "tui.plugins.section_catalog"
                        ),
                        disabled=True,
                    )
                )
            status = (
                t(row.status_key, **(row.status_args or {}))
                if row.status_args
                else t(row.status_key)
            )
            marker = "●" if row.enabled else "○"
            self._row_at.append(index)
            option_list.add_option(
                Option(t("tui.plugins.row", marker=marker, name=row.label, status=status))
            )
        if self._warnings:
            for warning in self._warnings:
                self._row_at.append(None)
                option_list.add_option(
                    Option(t("tui.plugins.warning", warning=warning), disabled=True)
                )
        self._restore_highlight(option_list, previous)
        installed = sum(1 for row in self._rows if row.section == "installed")
        catalog = len(self._rows) - installed
        self.query_one("#dialog-title", Static).update(
            t(
                "tui.plugins.manager_count",
                installed=installed,
                catalog=catalog,
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
        if previous is not None:
            option = self._option_for_row(previous)
            if option is not None:
                option_list.highlighted = option
                return
        if option_list.highlighted is None:
            option_list.highlighted = self._option_for_row(0)

    def _selected(self) -> PluginRow | None:
        option_list = self.query_one("#plugins-list", OptionList)
        row_index = self._selected_row_index(option_list)
        if row_index is None:
            return None
        return self._rows[row_index]

    # -- actions ------------------------------------------------------------

    @on(OptionList.OptionSelected)
    def _open_selected(self, _event: OptionList.OptionSelected) -> None:
        self.action_toggle()

    def action_toggle(self) -> None:
        row = self._selected()
        if row is None or row.section != "installed":
            return
        from zenagent.plugins.state import set_enabled

        self._write(row, lambda: set_enabled(row.id, not row.enabled))

    def action_install(self) -> None:
        row = self._selected()
        if row is None or row.section != "catalog":
            return
        if row.enabled:
            self.notify(t("tui.plugins.already_installed", name=row.label), timeout=3)
            return
        from zenagent.plugins.install import install_builtin

        self._write(
            row,
            lambda: install_builtin(row.id),
            note=t("tui.plugins.installed", name=row.label),
        )

    def action_uninstall(self) -> None:
        row = self._selected()
        if row is None or row.section != "installed":
            return
        from zenagent.plugins.install import uninstall_plugin

        self.app.push_screen(
            Confirm(t("tui.plugins.uninstall_confirm", name=row.label)),
            lambda ok: ok
            and self._write(
                row,
                lambda: uninstall_plugin(row.id),
                note=t("tui.plugins.uninstalled", name=row.label),
            ),
        )

    def _write(self, row: PluginRow, mutate, note: str | None = None) -> None:
        try:
            mutate()
        except (ValueError, OSError) as exc:
            self.notify(t("tui.plugins.failed", error=exc), timeout=5)
            return
        if note is not None:
            self.notify(note, timeout=3)
        self.run_worker(self._apply(row))

    async def _apply(self, row: PluginRow) -> None:
        """Re-apply the live runtime; defer while a turn is streaming.

        The wire path defers the same way, so a TUI toggle during a run lands
        at the same moment a desktop toggle would.
        """
        try:
            await self.runtime.reconfigure_plugins()
        except RuntimeError:
            self.notify(t("tui.plugins.apply_pending"), timeout=4)
            agent = getattr(self.runtime, "agent", None)
            while agent is not None and getattr(agent, "is_streaming", False):
                await asyncio.sleep(0.5)
            try:
                await self.runtime.reconfigure_plugins()
            except Exception as exc:  # noqa: BLE001 - a deferred apply still reports
                self.notify(t("tui.plugins.apply_failed", error=exc), timeout=5)
                return
        self._refresh()

    def action_close(self) -> None:
        self.dismiss(None)
