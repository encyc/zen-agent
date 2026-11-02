"""A compact, self-drawn tool-call card.

Deliberately not a ``Collapsible``: the built-in widget kept its body laid out
while "collapsed" (a 247-row card). This is omp's *plain tinted* topology --
a status head, an optional state tint, and the tail of the output -- drawn from
one ``Text``. A collapsed card is one row, except ``write_file``/``edit_file``:
those preview the change's first lines (``MAX_DIFF_PREVIEW_LINES``) so a file
mutation reads at a glance instead of only its one-line result sentence.
"""

from __future__ import annotations

from rich.cells import cell_len
from rich.text import Text
from textual.widgets import Static

from zenagent.app.views import ToolCallView
from zenagent.cli.tui import palette
from zenagent.i18n import t

__all__ = ["MAX_DIFF_PREVIEW_LINES", "MAX_RESULT_LINES", "ToolCard"]


MAX_RESULT_LINES = 12

#: Diff body lines previewed under a write/edit card before it is opened. The
#: card head already names the path, so ``---``/``+++`` heads never enter it.
MAX_DIFF_PREVIEW_LINES = 5

#: Tools whose collapsed card previews the change (omp's Edit/Create cards).
_FILE_MUTATION_TOOLS = frozenset({"write_file", "edit_file"})

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


def _diff_line_style(line: str) -> str:
    """Colour one diff row: additions ``OK``, removals ``ERROR``, rest muted."""
    if line.startswith("+"):
        return palette.OK
    if line.startswith("-"):
        return palette.ERROR
    return palette.MUTED


def _diff_preview_lines(lines: list[str]) -> list[str]:
    """Rows worth previewing: hunk markers and the source cap are not changes."""
    return [
        line
        for line in lines
        if not line.startswith("@@") and not line.startswith("... [")
    ]


class ToolCard(Static):
    """A collapsed card is one head row; write/edit add a bounded diff preview.

    Opened, it shows the tail of the result and the whole diff.
    """

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

    def _diff_lines(self) -> list[str]:
        """Display rows of the applied change, without its ``---``/``+++`` heads.

        The card head already names the path, so repeating the file heads would
        only spend rows; ``@@`` markers stay for the full view (the compact
        preview filters them out).
        """
        if not self.view.diff:
            return []
        lines = self.view.diff.splitlines()
        start = 1 if lines and lines[0].startswith("--- ") else 0
        if len(lines) > start and lines[start].startswith("+++ "):
            start += 1
        return lines[start:]

    def _append_diff_preview(self, text: Text) -> None:
        """Add the first lines of a write/edit change under its head row."""
        if self.view.name not in _FILE_MUTATION_TOOLS:
            return
        body = _diff_preview_lines(self._diff_lines())
        if not body:
            return
        budget = max(8, self._width() - 3)  # the three-column body indent
        for line in body[:MAX_DIFF_PREVIEW_LINES]:
            text.append("\n   ")
            text.append(_clip_cells(line, budget), style=_diff_line_style(line))
        hidden = len(body) - MAX_DIFF_PREVIEW_LINES
        if hidden > 0:
            text.append(
                "\n   " + t("tui.tool.more_diff_lines", n=hidden), style=palette.DIM
            )

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
            self._append_diff_preview(text)
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
            for line in self._diff_lines():
                text.append("\n   ")
                text.append(line, style=_diff_line_style(line))
        return text
