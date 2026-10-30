"""Collapsible sidebar: session, model and runtime status."""

from __future__ import annotations

from textual.widgets import Static

__all__ = ["Sidebar"]

NARROW_WIDTH = 100


class Sidebar(Static):
    """Three stacked sections; hidden automatically on narrow terminals."""

    def __init__(self) -> None:
        super().__init__("", id="sidebar")

    def refresh_contents(self, chat, models, sessions) -> None:
        status = chat.status
        current = sessions.current().name
        lines = [
            "SESSION",
            f"  {current}",
            f"  {len(chat.transcript)} views",
            "",
            "MODEL",
            f"  {status.model_id}",
            f"  ctx {status.context_used}/{status.context_window}",
            f"  ${status.cost:.4f}",
            "",
            "RUNTIME",
            f"  {'running' if status.running else 'idle'}",
            f"  queued {status.follow_ups} · steering {status.steering}",
            f"  {len(models.entries())} models",
            "",
            "^B toggle · ^P commands",
        ]
        self.update("\n".join(lines))

    def apply_width(self, width: int) -> None:
        self.display = width >= NARROW_WIDTH
