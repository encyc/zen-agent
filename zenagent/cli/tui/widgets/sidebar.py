"""Collapsible sidebar: session, model and runtime status."""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

__all__ = ["Sidebar"]

NARROW_WIDTH = 100

_TEXT = "#d7e0e8"
_MUTED = "#8296a6"
_ACCENT = "#d9a86c"


class Sidebar(Static):
    """Three quiet sections; hidden automatically on narrow terminals."""

    def __init__(self) -> None:
        super().__init__("", id="sidebar")

    def refresh_contents(self, chat, models, sessions) -> None:
        status = chat.status
        current = sessions.current().name
        text = Text()

        def section(label: str) -> None:
            text.append(label, style="bold")
            text.append("\n", style=_MUTED)

        def row(value: str, style: str = _TEXT) -> None:
            text.append("  ")
            text.append(value, style=style)
            text.append("\n")

        section("Session")
        row(current)
        row(f"{len(chat.transcript)} messages", style=_MUTED)
        text.append("\n")

        section("Model")
        row(status.model_id, style=_ACCENT)
        row(f"context  {status.context_used}/{status.context_window}", style=_MUTED)
        row(f"spend    ${status.cost:.4f}", style=_MUTED)
        text.append("\n")

        section("Runtime")
        state = "running" if status.running else "idle"
        row(state)
        row(f"queued {status.follow_ups}   steering {status.steering}", style=_MUTED)
        row(f"{len(models.entries())} models", style=_MUTED)
        text.append("\n")
        text.append("ctrl+b  hide sidebar\n", style=_MUTED)
        text.append("ctrl+p  commands", style=_MUTED)
        self.update(text)

    def apply_width(self, width: int) -> None:
        self.display = width >= NARROW_WIDTH
