"""A collapsible tool-call card: one line collapsed, args/result expanded."""

from __future__ import annotations

from textual.widgets import Collapsible, Static

from zenagent.app.views import ToolCallView

__all__ = ["ToolCard"]

_STATUS_MARK = {"running": "…", "ok": "ok", "error": "error"}


class ToolCard(Collapsible):
    """Shows tool name + argument preview; expanding reveals output/diff."""

    can_focus = True
    BINDINGS = [("enter,space", "toggle_card", "Toggle")]  # noqa: RUF012 - Textual class API

    def __init__(self, view: ToolCallView) -> None:
        self.view = view
        super().__init__(
            Static(view.result_text or "(no output yet)"),
            title=self._title(view),
            collapsed=view.status != "error",
            id=f"tool-{view.call_id}",
        )

    def action_toggle_card(self) -> None:
        self.collapsed = not self.collapsed

    @staticmethod
    def _title(view: ToolCallView) -> str:
        status = _STATUS_MARK.get(view.status, view.status)
        duration = f" · {view.duration_ms}ms" if view.duration_ms is not None else ""
        return f"{view.name} {view.args_preview} · {status}{duration}"

    def refresh_view(self) -> None:
        view = self.view
        if hasattr(self, "title"):
            self.title = self._title(view)  # Collapsible exposes a title reactive
        body = view.result_text or "(no output yet)"
        if view.diff:
            body = f"{body}\n\n{view.diff}"
        for child in self.query(Static):
            child.update(body)
