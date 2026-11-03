"""Display-state toggles: thinking rows, tool cards, plan mode."""

from __future__ import annotations

from zenagent.cli.tui.widgets.transcript import TranscriptView
from zenagent.i18n import t


class DisplayMixin:
    """Card/tool visibility actions. Plan state (``_mode_before_plan``) stays on the App."""

    def action_cycle_thinking(self) -> None:
        levels = ["off", "low", "medium", "high"]
        agent = self.runtime.agent
        current = agent.thinking or "off"
        try:
            agent.thinking = levels[(levels.index(current) + 1) % len(levels)]
        except ValueError:
            agent.thinking = "medium"
        self._refresh_status()

    def action_toggle_thinking(self) -> None:
        """ctrl+t expands/collapses every thinking row (omp toggles the block)."""
        from zenagent.cli.tui.widgets.rows import ThinkingRow

        rows = list(self.query(ThinkingRow))
        if not rows:
            return
        expand = any(row.collapsed for row in rows)
        for row in rows:
            row.set_expanded(expand)

    def action_toggle_plan(self) -> None:
        """alt+shift+p: flip plan mode on, and back to the previous mode off."""
        if self.runtime.mode == "plan":
            mode = self._mode_before_plan or "ask"
            self._mode_before_plan = None
        else:
            self._mode_before_plan = self.runtime.mode
            mode = "plan"
        self.runtime.set_mode(mode)
        self.notify(t("tui.app.mode_set", mode=mode))
        self._refresh_status()

    def action_toggle_tool_visibility(self) -> None:
        """ctrl+shift+o: hide/show every tool card (omp's tools.toggleVisibility)."""
        transcript = self.query_one(TranscriptView)
        transcript.set_tools_hidden(not transcript.tools_hidden)

    def action_display_reset(self) -> None:
        """alt+l: restore the default display state after fiddling with cards."""
        from zenagent.cli.tui.widgets.rows import ThinkingRow

        transcript = self.query_one(TranscriptView)
        transcript.set_tools_hidden(False)
        for row in self.query(ThinkingRow):
            if not row.collapsed:
                row.action_toggle()
        transcript.scroll_end(animate=False)

    def action_toggle_tools(self) -> None:
        """Batch expand/collapse; single cards keep Enter/space (omp parity)."""
        from zenagent.cli.tui.widgets.tool_card import ToolCard

        cards = list(self.query(ToolCard))
        if not cards:
            return
        expand = any(card.collapsed for card in cards)
        for card in cards:
            card.set_collapsed(not expand)
