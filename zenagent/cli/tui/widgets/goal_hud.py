"""Goal HUD: one line in the dock, directly above the status band.

The goal loop is invisible otherwise: a session can be running turn after turn
with no prompt. This row shows the objective, status, budget and last verdict,
and hides itself when the session has no goal.
"""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

from zenagent.app.views import GoalView, goal_limit_text
from zenagent.cli.tui import palette
from zenagent.cli.tui.textutil import clip_cells
from zenagent.i18n import t

__all__ = ["MAX_OBJECTIVE_CHARS", "GoalHud"]

#: The objective fragment is clipped, never wrapped.
MAX_OBJECTIVE_CHARS = 48

_ICONS = {
    "active": "◎",
    "paused": "⏸",
    "blocked": "⊘",
    "budget_limited": "◼",
    "complete": "✓",
}


class GoalHud(Static):
    """``◎ goal · active · fix tests · 3/40 · 100/2000``; hidden without a goal."""

    DEFAULT_CSS = """
    GoalHud {
        height: 1;
        background: $surface;
        padding: 0 1;
    }
    """

    def __init__(self) -> None:
        super().__init__("", id="goal-hud")
        self._plain = ""
        self.display = False

    @property
    def plain(self) -> str:
        return self._plain

    def render_text(self) -> str:
        return self._plain

    def set_goal(self, view: GoalView | None) -> None:
        """Render one goal snapshot; ``None`` hides the row."""
        if view is None:
            self._plain = ""
            if self.is_mounted:
                self.update("")
            self.display = False
            return
        text = Text()
        text.append(f"{_ICONS.get(view.status, '•')} ", style=palette.ACCENT)
        text.append(t("tui.goal.title"), style=palette.ACCENT)
        text.append(" · ", style=palette.DIM)
        text.append(t(f"tui.goal.status.{view.status}"), style=palette.MUTED)
        text.append(" · ", style=palette.DIM)
        text.append(
            clip_cells(view.objective, MAX_OBJECTIVE_CHARS), style=palette.TEXT
        )
        text.append(" · ", style=palette.DIM)
        text.append(
            t(
                "tui.goal.budget",
                turns=view.turns_used,
                max_turns=goal_limit_text(view.max_turns),
                tokens=view.tokens_used,
                max_tokens=goal_limit_text(view.max_tokens),
            ),
            style=palette.MUTED,
        )
        if view.last_verdict:
            text.append(" · ", style=palette.DIM)
            text.append(
                t(
                    "tui.goal.last",
                    verdict=view.last_verdict,
                    reason=view.last_reason or "-",
                ),
                style=palette.MUTED,
            )
        self._plain = text.plain
        if self.is_mounted:
            self.update(text)
        self.display = True
