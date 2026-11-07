"""Entry point for the Textual frontend.

Owns the interactive session: attach the prepared view stack to the app,
route tool approvals through the broker, and surface the exit summary. The
frontend-neutral assembly lives in :mod:`zenagent.app.bootstrap`.
"""

from __future__ import annotations

from zenagent.app.bootstrap import PreparedFrontend
from zenagent.cli.tui.app import ZenAgentApp

__all__ = ["run_tui_session"]


async def run_tui_session(
    prepared: PreparedFrontend,
    *,
    theme: str | None = None,
    language: str | None = None,
    images: list | None = None,
) -> int:
    """Run the Textual app on a prepared frontend; returns the exit code."""
    app = ZenAgentApp(
        prepared.runtime,
        theme=theme,
        language=language,
        images=images,
    )
    prepared.broker.set_confirm(app.request_tool_approval)
    await app.run_async()
    if app.exit_summary:
        print(app.exit_summary)
    return 0
