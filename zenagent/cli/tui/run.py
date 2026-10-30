"""Entry point for the Textual frontend."""

from __future__ import annotations

from zenagent.app.chat import ChatController
from zenagent.app.runtime import Runtime
from zenagent.cli.tui.app import ZenAgentApp

__all__ = ["run_tui"]


def run_tui(runtime: Runtime, *, chat: ChatController | None = None) -> int:
    """Run the TUI (blocking) and return its exit code."""
    app = ZenAgentApp(runtime, chat=chat)
    app.run()
    if app.exit_summary:
        print(app.exit_summary)
    return 0
