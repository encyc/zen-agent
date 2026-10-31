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


def _first_line(value: object) -> str:
    text = str(value or "")
    return text.splitlines()[0] if text.splitlines() else ""


def _diff_stats(diff: str) -> tuple[int, int]:
    """``(added, removed)`` for a unified diff, ignoring the ``+++``/``---`` heads."""
    added = removed = 0
    for line in diff.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return added, removed


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

    def on_click(self) -> None:
        """Click the row to open it, like omp's tool cards."""
        self.action_toggle_card()

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
        detail = self._family_detail()
        if detail:
            budget = max(8, self._width() - len(text.plain) - 24)
            if len(detail) > budget:
                detail = detail[: max(1, budget - 1)] + "…"
            text.append("  ")
            text.append(detail, style=_DIM)
        text.append("  ·  ", style=_DIM)
        text.append(_STATUS_LABEL.get(view.status, view.status), style=_DIM)
        if view.duration_ms is not None:
            text.append(f" · {view.duration_ms}ms", style=_DIM)
        return text

    def _family_detail(self) -> str:
        """The one-line body after the tool name: omp's per-family head grammar.

        Falls back to the JSON argument preview for unknown tools and for
        history replay (which carries no ``details``).
        """
        view = self.view
        args = view.args or {}
        details = view.details or {}
        name = view.name

        if name == "bash":
            command = _first_line(args.get("command"))
            exit_code = details.get("exit_code")
            if exit_code is None:
                return command
            return f"{command}  ·  exit {exit_code}" if command else f"exit {exit_code}"

        path = details.get("path") or args.get("path")
        if name == "read_file":
            total = details.get("total_lines")
            parts = [value for value in (path, f"{total} lines" if total else "") if value]
            if details.get("truncated"):
                parts.append("truncated")
            return "  ·  ".join(parts)

        if name == "edit_file":
            added, removed = _diff_stats(str(details.get("diff") or ""))
            parts = [value for value in (path, f"⟨+{added}/-{removed}⟩" if details.get("diff") else "") if value]
            return "  ·  ".join(parts)

        if name == "write_file":
            content = args.get("content")
            lines = len(content.splitlines()) if isinstance(content, str) else 0
            parts = [value for value in (path, f"{lines} lines" if lines else "") if value]
            return "  ·  ".join(parts)

        if name == "grep":
            pattern = args.get("pattern")
            matches = details.get("matches")
            parts = [f"'{pattern}'" if pattern is not None else ""]
            if matches is not None:
                parts.append(f"{matches} matches")
            return "  ·  ".join(part for part in parts if part)

        if name == "glob":
            pattern = args.get("pattern")
            return f"'{pattern}'" if pattern is not None else ""

        return view.args_preview

    def _renderable(self) -> Text:
        text = self._head()
        if self.collapsed:
            if self.view.status == "running" and self.view.progress:
                hint = f"  ⚙ {self.view.progress}"
            else:
                hint = "  (enter: expand)"
            budget = self._width() - len(text.plain)
            if budget > 8:
                if len(hint) > budget:
                    hint = hint[: budget - 1] + "…"
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
            if self.view.status == "running" and self.view.progress:
                text.append(f"\n   ⚙ {self.view.progress}", style=_DIM)
            else:
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
