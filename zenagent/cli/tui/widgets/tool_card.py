"""A compact, self-drawn tool-call card.

Deliberately not a ``Collapsible``: the built-in widget kept its body laid out
while "collapsed" (a 247-row card). This is omp's *plain tinted* topology --
a status head, an optional state tint, and the tail of the output -- drawn from
one ``Text`` so the collapsed height is exactly one row.
"""

from __future__ import annotations

from rich.cells import cell_len
from rich.text import Text
from textual.widgets import Static

from zenagent.app.views import ToolCallView
from zenagent.cli.tui import palette
from zenagent.i18n import t

__all__ = ["MAX_RESULT_LINES", "ToolCard"]


MAX_RESULT_LINES = 12

_STATUS_KEYS = {
    "running": "tui.tool.status_running",
    "ok": "tui.tool.status_ok",
    "error": "tui.tool.status_error",
}


def _status_label(status: str) -> str:
    key = _STATUS_KEYS.get(status)
    return t(key) if key else status
_STATUS_DOT = {"running": ("◐", palette.ACCENT), "ok": ("●", palette.MUTED), "error": ("●", palette.ERROR)}


def _first_line(value: object) -> str:
    text = str(value or "")
    return text.splitlines()[0] if text.splitlines() else ""


def _clip_cells(text: str, budget: int) -> str:
    """Truncate ``text`` to at most ``budget`` terminal cells (CJK-safe)."""
    if budget <= 0:
        return ""
    if cell_len(text) <= budget:
        return text
    out: list[str] = []
    used = 0
    for char in text:
        width = cell_len(char)
        if used + width > budget - 1:  # leave one cell for the ellipsis
            break
        out.append(char)
        used += width
    return "".join(out) + "…"


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
        dot, dot_style = _STATUS_DOT.get(view.status, ("●", palette.MUTED))
        text = Text()
        text.append(f"{dot} ", style=dot_style)
        text.append(view.name, style=palette.TEXT)
        detail = self._family_detail()
        if detail:
            budget = max(8, self._width() - len(text.plain) - 24)
            if len(detail) > budget:
                detail = detail[: max(1, budget - 1)] + "…"
            text.append("  ")
            text.append(detail, style=palette.DIM)
        text.append("  ·  ", style=palette.DIM)
        text.append(_status_label(view.status), style=palette.DIM)
        if view.duration_ms is not None:
            text.append(f" · {view.duration_ms}ms", style=palette.DIM)
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
            exit_label = t("tui.tool.exit", code=exit_code)
            return f"{command}  ·  {exit_label}" if command else exit_label

        path = details.get("path") or args.get("path")
        if name == "read_file":
            total = details.get("total_lines")
            parts = [
                value
                for value in (
                    path,
                    t("tui.tool.lines", n=total) if total else "",
                )
                if value
            ]
            if details.get("truncated"):
                parts.append(t("tui.tool.truncated"))
            return "  ·  ".join(parts)

        if name == "edit_file":
            added, removed = _diff_stats(str(details.get("diff") or ""))
            parts = [value for value in (path, f"⟨+{added}/-{removed}⟩" if details.get("diff") else "") if value]
            return "  ·  ".join(parts)

        if name == "write_file":
            content = args.get("content")
            lines = len(content.splitlines()) if isinstance(content, str) else 0
            parts = [
                value
                for value in (
                    path,
                    t("tui.tool.lines", n=lines) if lines else "",
                )
                if value
            ]
            return "  ·  ".join(parts)

        if name == "grep":
            pattern = args.get("pattern")
            matches = details.get("matches")
            parts = [f"'{pattern}'" if pattern is not None else ""]
            if matches is not None:
                parts.append(t("tui.tool.matches", n=matches))
            return "  ·  ".join(part for part in parts if part)

        if name == "glob":
            pattern = args.get("pattern")
            return f"'{pattern}'" if pattern is not None else ""

        if name == "list_dir":
            count = details.get("entries") or details.get("count")
            return "  ·  ".join(
                part for part in (path, f"{count} entries" if count else "") if part
            )

        if name == "task":
            parts = [
                str(details.get("name") or ""),
                str(details.get("status") or ""),
            ]
            cost = details.get("cost")
            if isinstance(cost, (int, float)) and cost:
                parts.append(f"${cost:.4f}")
            return "  ·  ".join(part for part in parts if part)

        if name == "todo":
            op = details.get("op")
            phases = details.get("phases") or {}
            total = sum(
                len(phase.get("tasks") or [])
                for phase in (
                    phases.values() if isinstance(phases, dict) else phases
                )
                if isinstance(phase, dict)
            )
            parts = [str(op or "")]
            if total:
                parts.append(f"{total} tasks")
            return "  ·  ".join(part for part in parts if part)

        if name == "ask":
            answers = details.get("answers")
            questions = args.get("questions")
            count = len(answers) if isinstance(answers, list) else None
            if count is None and isinstance(questions, list):
                count = len(questions)
            return f"{count} answers" if count else ""

        if name == "wait":
            job_id = details.get("job_id") or args.get("job_id")
            status = details.get("status")
            exit_code = details.get("exit_code")
            parts = [str(job_id or ""), str(status or "")]
            if exit_code is not None:
                parts.append(t("tui.tool.exit", code=exit_code))
            return "  ·  ".join(part for part in parts if part)

        if name == "web_search":
            query = args.get("query")
            results = details.get("results")
            provider = details.get("provider")
            parts = [f"'{query}'" if query is not None else ""]
            if results is not None:
                parts.append(f"{results} results")
            if provider:
                parts.append(str(provider))
            return "  ·  ".join(part for part in parts if part)

        if name == "fetch":
            url = details.get("url") or args.get("url")
            method = details.get("method")
            parts = [str(url or ""), str(method or "")]
            return "  ·  ".join(part for part in parts if part)

        return view.args_preview

    def _renderable(self) -> Text:
        text = self._head()
        if self.collapsed:
            if self.view.status == "running" and self.view.progress:
                hint = f"  ⚙ {self.view.progress}"
                budget = self._width() - cell_len(text.plain)
                if budget > 8:
                    text.append(_clip_cells(hint, budget), style=palette.DIM)
            else:
                hint = t("tui.tool.expand_hint")
                if cell_len(text.plain) + cell_len(hint) <= self._width():
                    text.append(hint, style=palette.DIM)
            return text

        lines = (self.view.result_text or "").splitlines()
        if len(lines) > MAX_RESULT_LINES:
            dropped = len(lines) - MAX_RESULT_LINES
            text.append(
                "\n" + t("tui.tool.earlier_lines", n=dropped), style=palette.DIM
            )
            lines = lines[-MAX_RESULT_LINES:]
        for line in lines:
            text.append("\n   ")
            text.append(line, style=palette.MUTED)
        if not lines and not self.view.diff:
            if self.view.status == "running" and self.view.progress:
                text.append(f"\n   ⚙ {self.view.progress}", style=palette.DIM)
            else:
                text.append("\n   " + t("tui.tool.no_output"), style=palette.DIM)
        if self.view.diff:
            for line in self.view.diff.splitlines():
                text.append("\n   ")
                style = palette.MUTED
                if line.startswith("+"):
                    style = palette.OK
                elif line.startswith("-"):
                    style = palette.ERROR
                text.append(line, style=style)
        return text
