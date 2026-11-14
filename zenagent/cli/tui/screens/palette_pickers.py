"""Model and session palette pickers (moved from modals.py, audit P2).

Note: screens/pickers.py is the pre-existing palette *helpers* module —
a different thing; these are the concrete screens.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from textual.widgets import OptionList

from zenagent.app.models import ModelEntry
from zenagent.app.sessions import SessionEntry
from zenagent.cli.tui.screens.palette import PaletteItem, PaletteScreen
from zenagent.i18n import t


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
                            "%m-%d %H:%M", time.localtime(entry.updated_at)
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


