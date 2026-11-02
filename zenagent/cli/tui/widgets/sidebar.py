"""Sidebar work panel: session/model/runtime info plus live work sections.

This is the "what is happening right now" surface. The todo list has exactly
one visible home: the sidebar when it is visible, the dock HUD otherwise (see
``ZenAgentApp._sync_chrome``). Subagents and background jobs live here too --
rows are keyed so the 1-second refresh updates text in place instead of
rebuilding the widget tree.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from rich.text import Text
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Static

from zenagent.cli.tui import palette
from zenagent.cli.tui.textutil import clip_cells
from zenagent.i18n import t, tn

__all__ = [
    "MAX_JOB_ROWS",
    "MAX_SUBAGENT_ROWS",
    "NARROW_WIDTH",
    "Sidebar",
    "SidebarRow",
    "SidebarSection",
]

NARROW_WIDTH = 100

#: Rows rendered per live-work section (the rest stay in the store/manager).
MAX_SUBAGENT_ROWS = 5
MAX_JOB_ROWS = 6

#: Inner content width: ``#sidebar`` is 26 wide with ``padding: 1 2``.
_CONTENT_WIDTH = 22


_SUBAGENT_MARKS = {
    "running": "◐",
    "ok": "✓",
    "error": "✗",
    "aborted": "⊘",
    "truncated": "…",
    "interrupted": "⊘",
}

_TODO_CLOSED = frozenset({"completed", "abandoned", "done"})

_TODO_MARKS = {
    "completed": "✓",
    "done": "✓",
    "in_progress": "▶",
    "pending": "○",
    "blocked": "⊘",
    "abandoned": "-",
}

_JOB_MARKS = {
    "running": "●",
    "ok": "✓",
    "failed": "✗",
    "timed_out": "⏱",
}


def _clip(text: str, width: int = _CONTENT_WIDTH) -> str:
    """One sidebar row is one terminal line; never wrap (CJK is two cells)."""
    return clip_cells(text, width)


def _seconds(value: float) -> str:
    """Compact elapsed/duration label (``42s`` / ``3m`` / ``1.2h``)."""
    if value < 60:
        return f"{value:.0f}s"
    minutes = value / 60
    if minutes < 60:
        return f"{minutes:.0f}m"
    return f"{minutes / 60:.1f}h"


class SidebarRow(Static):
    """One sidebar line; keyed so refreshes update it in place."""

    DEFAULT_CSS = """
    SidebarRow {
        height: 1;
    }
    """

    def __init__(
        self,
        key: str,
        text: Text | str = "",
        *,
        on_click: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__("", classes="sidebar-row")
        self.key = key
        self._plain = ""
        self._on_click = on_click
        self.set_content(text)

    @property
    def plain(self) -> str:
        return self._plain

    def set_content(self, text: Text | str) -> None:
        self._plain = text.plain if isinstance(text, Text) else str(text)
        self.update(text)

    def on_click(self) -> None:
        if self._on_click is not None:
            self._on_click(self.key)


class SidebarSection(Vertical):
    """A titled group of keyed rows; hidden whenever it has no rows."""

    DEFAULT_CSS = """
    SidebarSection {
        height: auto;
        margin: 0 0 1 0;
    }
    SidebarSection .sidebar-title {
        height: 1;
        color: $muted;
        text-style: bold;
    }
    SidebarSection .sidebar-rows {
        height: auto;
    }
    """

    def __init__(
        self,
        section_key: str,
        *,
        on_row_click: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(classes="sidebar-section")
        #: Stable, locale-independent id (``session``, ``jobs`` …).
        self.key = section_key
        self._on_row_click = on_row_click
        self._rows: dict[str, SidebarRow] = {}
        self._rows_box = Vertical(classes="sidebar-rows")

    @property
    def title(self) -> str:
        """Localized section heading (English under the default locale)."""
        return t(f"tui.sidebar.{self.key}")

    def compose(self):
        yield Static(self.title, classes="sidebar-title")
        yield self._rows_box

    def on_mount(self) -> None:
        self._apply_title()

    def _apply_title(self) -> None:
        accent = palette.SECTION_ACCENTS.get(self.key, palette.MUTED)
        title = self.query_one(".sidebar-title", Static)
        title.update(self.title)
        title.styles.color = accent

    def refresh_language(self) -> None:
        """Re-render the heading after a locale switch (rows rebuild on repaint)."""
        self._apply_title()

    def set_rows(self, items: list[tuple[str, Text | str]]) -> None:
        """Reconcile this section's rows against ``items`` (key order = display)."""
        wanted = dict(items)
        for key in list(self._rows):
            if key not in wanted:
                row = self._rows.pop(key)
                row.remove()
        for key, content in items:
            row = self._rows.get(key)
            if row is None:
                row = SidebarRow(key, content, on_click=self._on_row_click)
                self._rows[key] = row
                self._rows_box.mount(row)
            else:
                row.set_content(content)
        self._sync_order([key for key, _ in items])
        self.display = bool(items)

    def _sync_order(self, keys: list[str]) -> None:
        children = list(self._rows_box.children)
        if [row.key for row in children if isinstance(row, SidebarRow)] == keys:
            return
        for index, key in enumerate(keys):
            row = self._rows.get(key)
            # ``mount()`` registers the row in ``children`` immediately, before
            # its Mount event, so a freshly added row can be positioned in the
            # same refresh instead of waiting for the next one.
            if row is not None and row in children:
                # Textual 8 makes ``before``/``after`` keyword-only; passing an
                # index positionally raises TypeError. ``before=index`` targets
                # the wanted slot, and walking in display order keeps the
                # already-placed prefix stable.
                self._rows_box.move_child(row, before=index)


class Sidebar(VerticalScroll):
    """The work panel; hover/click handlers are injected by the app."""

    def __init__(self) -> None:
        super().__init__(id="sidebar")
        #: Filled from the app on every repaint; drag width - chrome.
        self._content_width = _CONTENT_WIDTH
        self._on_todo_click: Callable[[], None] | None = None
        self._on_subagent_click: Callable[[str], None] | None = None
        self._on_job_click: Callable[[str], None] | None = None
        self._on_context_click: Callable[[], None] | None = None

    def compose(self):
        yield SidebarSection("session")
        yield SidebarSection("model")
        yield SidebarSection("runtime")
        yield SidebarSection("context", on_row_click=lambda _key: self._context_clicked())
        yield SidebarSection("todo", on_row_click=lambda _key: self._todo_clicked())
        yield SidebarSection("subagent", on_row_click=self._subagent_clicked)
        yield SidebarSection("jobs", on_row_click=self._job_clicked)

    def refresh_language(self) -> None:
        """Repaint every section heading for the active locale."""
        for section in self.query(SidebarSection):
            section.refresh_language()

    # -- wiring -------------------------------------------------------------

    def set_handlers(
        self,
        *,
        on_todo: Callable[[], None] | None = None,
        on_subagent: Callable[[str], None] | None = None,
        on_job: Callable[[str], None] | None = None,
        on_context: Callable[[], None] | None = None,
    ) -> None:
        """Install click handlers (None clears one)."""
        self._on_todo_click = on_todo
        self._on_subagent_click = on_subagent
        self._on_job_click = on_job
        self._on_context_click = on_context

    def _todo_clicked(self) -> None:
        if self._on_todo_click is not None:
            self._on_todo_click()

    def _subagent_clicked(self, key: str) -> None:
        if self._on_subagent_click is not None:
            self._on_subagent_click(key)

    def _job_clicked(self, key: str) -> None:
        if self._on_job_click is not None:
            self._on_job_click(key)

    def _context_clicked(self) -> None:
        if self._on_context_click is not None:
            self._on_context_click()

    # -- data ---------------------------------------------------------------

    def _section(self, name: str) -> SidebarSection:
        for section in self.query(SidebarSection):
            if name in (section.key, section.title):
                return section
        raise AssertionError(f"sidebar has no {name!r} section")

    def refresh_contents(self, chat, models, *, width: int | None = None) -> None:
        """Repaint every section from the one status snapshot.

        Reads only the snapshot: ``SessionManager.current()`` eagerly touches
        the session file, and a repaint must never resurrect a deleted one.
        """
        if width is not None:
            # 1 border + 2+2 padding are chrome, not content.
            self._content_width = max(12, int(width) - 5)
        status = chat.status

        def fit(value: str) -> str:
            return _clip(value, self._content_width)

        self._section("session").set_rows(
            [
                ("name", Text(fit(status.session_name), style=palette.TEXT)),
                ("messages", Text(tn("tui.sidebar.messages", len(chat.transcript)), style=palette.MUTED)),
            ]
        )
        self._section("model").set_rows(
            [
                ("model", Text(fit(status.model_id), style=palette.ACCENT)),
                (
                    "context",
                    Text(
                        t(
                            "tui.sidebar.ctx",
                            used=status.context_used,
                            window=status.context_window,
                        ),
                        style=palette.MUTED,
                    ),
                ),
                ("spend", Text(t("tui.sidebar.spend", cost=f"{status.cost:.4f}"), style=palette.MUTED)),
            ]
        )
        self._section("runtime").set_rows(
            [
                ("state", Text(
                    t("tui.sidebar.state_running")
                    if status.running
                    else t("tui.sidebar.state_idle")
                )),
                (
                    "queue",
                    Text(
                        t(
                            "tui.sidebar.queue",
                            queued=status.follow_ups,
                            steer=status.steering,
                        ),
                        style=palette.MUTED,
                    ),
                ),
                ("models", Text(tn("tui.sidebar.models", len(models.entries())), style=palette.MUTED)),
            ]
        )

        report = status.context
        if report is not None and report.slices:
            self._section("context").set_rows(_context_rows(report, self._content_width))
        else:
            # Match the section key, not the (localized) heading: "Context"
            # only resolves under the English locale.
            self._section("context").set_rows([])

        # Every task is listed inline (one clipped line each, phases as headers);
        # the overlay stays the place where full text is readable.
        todo_rows: list[tuple[str, Text | str]] = []
        phase_style = f"bold {palette.SECTION_ACCENTS['todo']}"
        for phase_index, phase in enumerate(status.todos):
            tasks = phase.get("tasks") or []
            name = str(phase.get("name") or t("tui.todo.tasks"))
            closed = sum(
                1 for task in tasks if task.get("status") in _TODO_CLOSED
            )
            todo_rows.append(
                (
                    f"phase:{phase_index}",
                    Text(fit(f"{name} {closed}/{len(tasks)}"), style=phase_style),
                )
            )
            for task_index, task in enumerate(tasks):
                state = str(task.get("status") or "pending")
                mark = _TODO_MARKS.get(state, "○")
                color = palette.STATE_COLORS.get(state, palette.STATE_COLORS["pending"])
                label = f"{mark} {task.get('content') or ''}"
                todo_rows.append(
                    (f"task:{phase_index}:{task_index}", Text(fit(label), style=color))
                )
        self._section("todo").set_rows(todo_rows)

        subagents = []
        for view in status.subagents[:MAX_SUBAGENT_ROWS]:
            mark = _SUBAGENT_MARKS.get(view.status, "•")
            label = f"{mark} {view.name} {_seconds(view.duration_ms / 1000)}"
            if view.transcript_truncated:
                label += t("tui.sidebar.transcript_cut")
            style = palette.STATE_COLORS.get(view.status, palette.TEXT)
            subagents.append((view.call_id, Text(fit(label), style=style)))
        self._section("subagent").set_rows(subagents)

        jobs = []
        now = time.time()
        for job in status.jobs[:MAX_JOB_ROWS]:
            mark = _JOB_MARKS.get(job.status, "•")
            elapsed = (
                job.duration_ms / 1000
                if job.duration_ms is not None
                else max(0.0, now - job.started_at)
            )
            label = f"{mark} {job.id} {_seconds(elapsed)}"
            style = palette.STATE_COLORS.get(job.status, palette.MUTED)
            jobs.append((job.id, Text(fit(label), style=style)))
        self._section("jobs").set_rows(jobs)

    def apply_width(self, width: int) -> bool:
        """Whether the panel fits; the app owns the actual ``display`` flag."""
        return width >= NARROW_WIDTH


def _context_rows(report, width: int) -> list[tuple[str, Text | str]]:
    """Total line, a 20-cell stacked bar and the two biggest slices."""
    window = report.window
    pct = report.total_tokens / window * 100.0 if window else 0.0
    mark = "" if report.measured else "≈"
    total_line = t(
        "tui.sidebar.ctx_total",
        pct=f"{pct:.0f}",
        mark=mark,
        used=_human(report.total_tokens),
        window=_human(window),
    )
    biggest = sorted(report.slices, key=lambda item: item.tokens, reverse=True)[:2]
    top = " · ".join(
        f"{_short(item.id)} {_fmt_pct(item.pct)}" for item in biggest
    )
    return [
        ("ctx-total", Text(_clip(total_line, width), style=palette.TEXT)),
        ("ctx-bar", _context_bar(report, max(6, width - 2))),
        ("ctx-top", Text(_clip(top, width), style=palette.MUTED)),
    ]


def _context_bar(report, width: int) -> Text:
    """A stacked bar of the slices across the model window; the rest is free.

    ``pct`` is a share of the window, so cells are allocated by it directly and
    the remainder stays empty (░) instead of being handed to the last slice.
    """
    text = Text()
    remaining = width
    for item in report.slices:
        cells = round(item.pct / 100.0 * width)
        cells = max(0, min(cells, remaining))
        if cells:
            color = palette.CONTEXT_COLORS.get(item.id, palette.DIM)
            text.append("█" * cells, style=color)
            remaining -= cells
    if remaining > 0:
        text.append("░" * remaining, style=palette.DIM)
    return text


def _fmt_pct(value: float) -> str:
    """Small window shares keep one decimal; full percents stay integers."""
    return f"{value:.1f}%" if value < 1 else f"{value:.0f}%"


def _human(count: int) -> str:
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}m"
    if count >= 1_000:
        return f"{count / 1_000:.0f}k"
    return str(count)


def _short(slice_id: str) -> str:
    """Compact context-slice label (falls back to the stable id)."""
    key = f"tui.sidebar.short.{slice_id}"
    value = t(key)
    return slice_id if value == key else value
