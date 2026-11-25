"""Goal panel modal: the session goal's full fields, behind the GoalHud click.

The HUD shows one clipped line; this is the rest of what the desktop's goal
panel shows — objective, check, the three budgets with what they spent, and
the last verdict with its reason. Pause/resume/clear reuse the ``/goal``
command path (shared with the REPL and the wire ``command`` op), so the panel
adds no second way to mutate goal state.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static

from zenagent.app.views import GoalView
from zenagent.cli.tui.screens.dialogs import DialogScreen
from zenagent.i18n import t

__all__ = ["GoalPanel"]

_ICONS = {
    "active": "◎",
    "paused": "⏸",
    "blocked": "⊘",
    "budget_limited": "◼",
    "complete": "✓",
}


class GoalPanel(DialogScreen[None]):
    """Read the snapshot, act through the ``/goal`` command path."""

    BINDINGS = [  # noqa: RUF012 - Textual class API
        ("p", "toggle_goal", "Pause/Resume"),
        ("c", "clear", "Clear"),
        ("escape", "close", "Close"),
    ]

    def __init__(self, app) -> None:
        super().__init__()
        self._app = app

    def on_mount(self) -> None:
        # The panel has no focusable widget; without this the composer input
        # keeps focus and eats every key the panel's bindings need.
        self.set_focus(None)

    def _view(self) -> GoalView | None:
        return self._app.chat.status.goal

    # -- layout -------------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(t("tui.goal.panel_title"), id="dialog-title")
            yield Static(self._body(), classes="dialog-detail")
            yield Static(t("tui.goal.panel_hint"), id="dialog-hint")

    def _body(self) -> str:
        view = self._view()
        if view is None:
            return t("cmdmsg.goal_none")
        icon = _ICONS.get(view.status, "•")
        lines = [
            t("tui.goal.panel_objective", icon=icon, objective=view.objective),
            t("tui.goal.panel_status", status=t(f"tui.goal.status.{view.status}")),
        ]
        if view.check:
            lines.append(t("tui.goal.panel_check", check=view.check))
        lines.append(
            t(
                "tui.goal.panel_budget",
                turns=str(view.turns_used),
                tokens=str(view.tokens_used),
                wall=f"{int(view.wall_seconds)}s",
            )
        )
        if view.last_verdict:
            lines.append(
                t(
                    "tui.goal.last",
                    verdict=view.last_verdict,
                    reason=view.last_reason or "-",
                )
            )
        return "\n".join(lines)

    # -- actions ------------------------------------------------------------

    def action_toggle_goal(self) -> None:
        view = self._view()
        if view is None:
            return
        argument = "resume" if view.status == "paused" else "pause"
        self._dispatch(argument)

    def action_clear(self) -> None:
        if self._view() is None:
            return
        from zenagent.cli.tui.screens.modals import Confirm

        def clear(ok: bool | None) -> None:
            if ok:
                self._dispatch("clear")

        self.app.push_screen(Confirm(t("tui.goal.clear_confirm")), clear)

    def _dispatch(self, argument: str) -> None:
        self.run_worker(self._run(argument))

    async def _run(self, argument: str) -> None:
        await self._app._dispatch_command("goal", argument)
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)
