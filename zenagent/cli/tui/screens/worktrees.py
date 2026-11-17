"""Worktrees modal: the TUI's ``/worktrees`` overview.

Read-only over :func:`zenagent.app.workspace.worktree_overview`. Switching is
deliberately not offered: a TUI process is bound to its cwd, so moving to a
worktree means restarting with ``--cwd`` — the panel says so instead of
pretending.
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from zenagent.app.workspace import worktree_overview
from zenagent.cli.tui.screens.dialogs import DialogScreen
from zenagent.i18n import t

__all__ = ["WorktreesScreen"]


class WorktreesScreen(DialogScreen[None]):
    """The repository's worktrees; the current one is marked."""

    BINDINGS = [("escape", "close", "Close")]  # noqa: RUF012 - Textual class API

    def __init__(self, runtime) -> None:
        super().__init__()
        self.runtime = runtime

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(t("tui.worktrees.title"), id="dialog-title")
            with Vertical(id="dialog-body"):
                yield OptionList(id="worktrees-list")
            yield Static(t("tui.worktrees.hint"), id="dialog-hint")

    def on_mount(self) -> None:
        option_list = self.query_one("#worktrees-list", OptionList)
        overview = worktree_overview(self.runtime.root)
        if not overview["is_repo"]:
            option_list.add_option(
                Option(t("tui.worktrees.not_repo", root=overview["root"]), disabled=True)
            )
        else:
            option_list.add_option(
                Option(
                    t(
                        "tui.worktrees.head",
                        branch=overview["branch"] or "—",
                        main=overview["main"],
                    ),
                    disabled=True,
                )
            )
            for entry in overview["worktrees"]:
                mark = "▸" if entry["current"] else " "
                branch = entry["branch"] or (
                    f"({t('tui.worktrees.detached')} {entry['head'][:7]})"
                )
                note = " ·bare" if entry["bare"] else ""
                option_list.add_option(
                    Option(
                        t(
                            "tui.worktrees.row",
                            mark=mark,
                            branch=branch,
                            path=entry["path"],
                            note=note,
                        )
                    )
                )
        option_list.focus()

    @on(OptionList.OptionSelected)
    def _open_selected(self, _event: OptionList.OptionSelected) -> None:
        # Read-only: a row carries no action; switching means a restart.
        self.notify(t("tui.worktrees.switch_note"), timeout=5)

    def action_close(self) -> None:
        self.dismiss(None)
