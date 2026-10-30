"""A compact, self-drawn tool-call card.

Deliberately not a ``Collapsible``: the built-in widget kept its body laid out
while "collapsed" (a 247-row card). This is omp's *plain tinted* topology --
a status head, an optional state tint, and the tail of the output -- drawn from
one ``Text`` so the collapsed height is exactly one row.
"""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

from zenagent.app.views import ToolCallView

__all__ = ["MAX_RESULT_LINES", "ToolCard"]

_TEXT = "#d7e0e8"
_MUTED = "#8296a6"
_DIM = "#5d6f7e"
_ACCENT = "#d9a86c"
_ERROR = "#e08787"
_ADDED = "#8fbf8f"

MAX_RESULT_LINES = 12

_STATUS_LABEL = {"running": "running", "ok": "ok", "error": "failed"}
_STATUS_DOT = {"running": ("◐", _ACCENT), "ok": ("●", _MUTED), "error": ("●", _ERROR)}


class ToolCard(Static):
    """One row while collapsed; the tail of the output when opened."""

    can_focus = True
    BINDINGS = [("enter,space", "toggle_card", "Expand")]  # noqa: RUF012 - Textual class API

    def __init__(self, view: ToolCallView) -> None:
        self.view = view
        self.collapsed = view.status != "error"
        super().__init__(self._renderable(), classes=self._tint_class())

    @property
    def plain(self) -> str:
        return self._renderable().plain

    # -- state --------------------------------------------------------------

    def _tint_class(self) -> str:
        return f"tool-card -{self.view.status}"

    def set_collapsed(self, collapsed: bool) -> None:
        self.collapsed = collapsed
        self._refresh()

    def action_toggle_card(self) -> None:
        self.collapsed = not self.collapsed
        self._refresh()

    def refresh_view(self) -> None:
        self._refresh()

    def _refresh(self) -> None:
        if not self.is_mounted:
            return
        self.set_classes(self._tint_class())
        self.update(self._renderable())

    # -- rendering ----------------------------------------------------------

    def _width(self) -> int:
        try:
            width = self.size.width
        except RuntimeError:  # not mounted yet: render against a default width
            width = 0
        return max(24, (width or 80) - 2)

    def _head(self) -> Text:
        view = self.view
        dot, dot_style = _STATUS_DOT.get(view.status, ("●", _MUTED))
        text = Text()
        text.append(f"{dot} ", style=dot_style)
        text.append(view.name, style=_TEXT)
        args = view.args_preview
        if args:
            budget = max(8, self._width() - len(view.name) - 24)
            if len(args) > budget:
                args = args[: max(1, budget - 1)] + "…"
            text.append("  ")
            text.append(args, style=_DIM)
        text.append("  ·  ", style=_DIM)
        text.append(_STATUS_LABEL.get(view.status, view.status), style=_DIM)
        if view.duration_ms is not None:
            text.append(f" · {view.duration_ms}ms", style=_DIM)
        return text

    def _renderable(self) -> Text:
        text = self._head()
        if self.collapsed:
            hint = "  (enter: expand)"
            if len(text.plain) + len(hint) <= self._width():
                text.append(hint, style=_DIM)
            return text

        lines = (self.view.result_text or "").splitlines()
        if len(lines) > MAX_RESULT_LINES:
            dropped = len(lines) - MAX_RESULT_LINES
            text.append(f"\n   … {dropped} earlier lines", style=_DIM)
            lines = lines[-MAX_RESULT_LINES:]
        for line in lines:
            text.append("\n   ")
            text.append(line, style=_MUTED)
        if not lines and not self.view.diff:
            text.append("\n   (no output)", style=_DIM)
        if self.view.diff:
            for line in self.view.diff.splitlines():
                text.append("\n   ")
                style = _MUTED
                if line.startswith("+"):
                    style = _ADDED
                elif line.startswith("-"):
                    style = _ERROR
                text.append(line, style=style)
        return text
