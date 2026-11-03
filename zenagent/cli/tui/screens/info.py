"""Read-only info screens (todo checklist, context breakdown) and their push."""

from __future__ import annotations

from zenagent.cli.tui.widgets.prompt_input import PromptInput
from zenagent.i18n import t


def show_todo_list(app) -> None:
        """Open the read-only checklist behind the one-line HUD summary."""
        from zenagent.cli.tui.screens.todo_list import TodoListScreen

        phases = app.chat.todos
        if not phases:
            app.notify(t("tui.app.no_todo"))
            return

        def restore_focus(_result=None) -> None:
            app.query_one(PromptInput).focus()

        app.push_screen(TodoListScreen(phases), restore_focus)



def show_context_breakdown(app) -> None:
        """Open the full context split (sidebar row click / palette caller)."""
        from zenagent.cli.tui.screens.context_breakdown import ContextBreakdownScreen

        report = app.chat.status.context
        if report is None:
            app.notify(t("tui.app.no_context"))
            return

        def restore_focus(_result=None) -> None:
            app.query_one(PromptInput).focus()

        app.push_screen(ContextBreakdownScreen(report), restore_focus)

