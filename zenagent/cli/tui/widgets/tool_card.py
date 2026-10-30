"""A compact, self-drawn tool-call card.

Deliberately not a Collapsible: the built-in widget kept the body laid out while
"collapsed" (a 247-row card), and a single Text row is both cheaper and easier to
style (quiet title, dim output, warm accent only on focus).
"""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

from zenagent.app.views import ToolCallView

__all__ = ["ToolCard"]

_TEXT = "#d7e0e8"
_MUTED = "#8296a6"
_ACCENT = "#d9a86c"
_ERROR = "#e08787"

_STATUS = {"running": "running", "ok": "ok", "error": "failed"}


class ToolCard(Static):
    """One line collapsed; Enter/space reveals the full result."""

    can_focus = True
    BINDINGS = [("enter,space", "toggle_card", "Toggle")]  # noqa: RUF012 - Textual class API

    def __init__(self, view: ToolCallView) -> None:
        self.view = view
        self.collapsed = view.status != "error"
        super().__init__(self._renderable(), classes="tool-card")

    @property
    def title_text(self) -> str:
        view = self.view
        status = _STATUS.get(view.status, view.status)
        duration = f" · {view.duration_ms}ms" if view.duration_ms is not None else ""
        return f"{view.name}  {view.args_preview}  ·  {status}{duration}"

    def _renderable(self) -> Text:
        text = Text()
        marker = "▸ " if self.collapsed else "▾ "
        colour = _ERROR if self.view.status == "error" else _MUTED
        text.append(marker, style=_ACCENT)
        text.append(self.title_text, style=colour)
        if not self.collapsed:
            body = self.view.result_text or "(no output)"
            for line in body.splitlines():
                text.append("\n    ")
                text.append(line, style=_MUTED)
            if self.view.diff:
                for line in self.view.diff.splitlines():
                    text.append("\n    ")
                    text.append(line, style=_MUTED)
        return text

    def action_toggle_card(self) -> None:
        self.collapsed = not self.collapsed
        self.update(self._renderable())

    def refresh_view(self) -> None:
        self.update(self._renderable())
