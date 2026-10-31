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

_TEXT = palette.TEXT
_MUTED = palette.MUTED
_ACCENT = palette.ACCENT

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
    "completed": ("✓", palette.STATE_COLORS["completed"]),
    "done": ("✓", palette.STATE_COLORS["done"]),
    "in_progress": ("▶", palette.STATE_COLORS["in_progress"]),
    "pending": ("○", palette.STATE_COLORS["pending"]),
    "blocked": ("⊘", palette.STATE_COLORS["blocked"]),
    "abandoned": ("-", palette.STATE_COLORS["abandoned"]),
}

_JOB_MARKS = {
    "running": "●",
    "ok": "✓",
    "failed": "✗",
    "timed_out": "⏱",
}


def _clip(text: str, width: int = _CONTENT_WIDTH) -> str:
    """One sidebar row is one terminal line; never wrap."""
    return text if len(text) <= width else text[: width - 1] + "…"


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
        color: #8296a6;
        text-style: bold;
    }
    SidebarSection .sidebar-rows {
        height: auto;
    }
    """

    def __init__(
        self,
        title: str,
        *,
        on_row_click: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(classes="sidebar-section")
        self.title = title
        self._on_row_click = on_row_click
        self._rows: dict[str, SidebarRow] = {}
        self._rows_box = Vertical(classes="sidebar-rows")

    def compose(self):
        yield Static(self.title, classes="sidebar-title")
        yield self._rows_box

    def on_mount(self) -> None:
        accent = palette.SECTION_ACCENTS.get(self.title, palette.MUTED)
        self.query_one(".sidebar-title", Static).styles.color = accent

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
        current = [
            row.key for row in self._rows_box.children if isinstance(row, SidebarRow)
        ]
        if current == keys:
            return
        for index, key in enumerate(keys):
            row = self._rows.get(key)
            if row is not None and row.is_mounted:
                self._rows_box.move_child(row, index)


class Sidebar(VerticalScroll):
    """The work panel; hover/click handlers are injected by the app."""

    def __init__(self) -> None:
        super().__init__(id="sidebar")
        self._on_todo_click: Callable[[], None] | None = None
        self._on_subagent_click: Callable[[str], None] | None = None
        self._on_job_click: Callable[[str], None] | None = None

    def compose(self):
        yield SidebarSection("Session")
        yield SidebarSection("Model")
        yield SidebarSection("Runtime")
        yield SidebarSection("Todo", on_row_click=lambda _key: self._todo_clicked())
        yield SidebarSection("Subagent", on_row_click=self._subagent_clicked)
        yield SidebarSection("Jobs", on_row_click=self._job_clicked)

    # -- wiring -------------------------------------------------------------

    def set_handlers(
        self,
        *,
        on_todo: Callable[[], None] | None = None,
        on_subagent: Callable[[str], None] | None = None,
        on_job: Callable[[str], None] | None = None,
    ) -> None:
        """Install click handlers (None clears one)."""
        self._on_todo_click = on_todo
        self._on_subagent_click = on_subagent
        self._on_job_click = on_job

    def _todo_clicked(self) -> None:
        if self._on_todo_click is not None:
            self._on_todo_click()

    def _subagent_clicked(self, key: str) -> None:
        if self._on_subagent_click is not None:
            self._on_subagent_click(key)

    def _job_clicked(self, key: str) -> None:
        if self._on_job_click is not None:
            self._on_job_click(key)

    # -- data ---------------------------------------------------------------

    def _section(self, title: str) -> SidebarSection:
        for section in self.query(SidebarSection):
            if section.title == title:
                return section
        raise AssertionError(f"sidebar has no {title!r} section")

    def refresh_contents(self, chat, models) -> None:
        """Repaint every section from the one status snapshot.

        Reads only the snapshot: ``SessionManager.current()`` eagerly touches
        the session file, and a repaint must never resurrect a deleted one.
        """
        status = chat.status

        self._section("Session").set_rows(
            [
                ("name", Text(_clip(status.session_name), style=_TEXT)),
                ("messages", Text(f"{len(chat.transcript)} messages", style=_MUTED)),
            ]
        )
        self._section("Model").set_rows(
            [
                ("model", Text(_clip(status.model_id), style=_ACCENT)),
                (
                    "context",
                    Text(
                        _clip(f"ctx {status.context_used}/{status.context_window}"),
                        style=_MUTED,
                    ),
                ),
                ("spend", Text(f"spend ${status.cost:.4f}", style=_MUTED)),
            ]
        )
        self._section("Runtime").set_rows(
            [
                ("state", Text("running" if status.running else "idle")),
                (
                    "queue",
                    Text(
                        _clip(
                            f"queued {status.follow_ups} · steer {status.steering}"
                        ),
                        style=_MUTED,
                    ),
                ),
                ("models", Text(f"{len(models.entries())} models", style=_MUTED)),
            ]
        )

        # Every task is listed inline (one clipped line each, phases as headers);
        # the overlay stays the place where full text is readable.
        todo_rows: list[tuple[str, Text | str]] = []
        phase_style = f"bold {palette.SECTION_ACCENTS['Todo']}"
        for phase_index, phase in enumerate(status.todos):
            tasks = phase.get("tasks") or []
            name = str(phase.get("name") or "Tasks")
            closed = sum(
                1 for task in tasks if task.get("status") in _TODO_CLOSED
            )
            todo_rows.append(
                (
                    f"phase:{phase_index}",
                    Text(_clip(f"{name} {closed}/{len(tasks)}"), style=phase_style),
                )
            )
            for task_index, task in enumerate(tasks):
                state = str(task.get("status") or "pending")
                mark, color = _TODO_MARKS.get(
                    state, ("○", palette.STATE_COLORS["pending"])
                )
                label = f"{mark} {task.get('content') or ''}"
                todo_rows.append(
                    (f"task:{phase_index}:{task_index}", Text(_clip(label), style=color))
                )
        self._section("Todo").set_rows(todo_rows)

        subagents = []
        for view in status.subagents[:MAX_SUBAGENT_ROWS]:
            mark = _SUBAGENT_MARKS.get(view.status, "•")
            label = f"{mark} {view.name} {_seconds(view.duration_ms / 1000)}"
            style = palette.STATE_COLORS.get(view.status, _TEXT)
            subagents.append((view.call_id, Text(_clip(label), style=style)))
        self._section("Subagent").set_rows(subagents)

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
            style = palette.STATE_COLORS.get(job.status, _MUTED)
            jobs.append((job.id, Text(_clip(label), style=style)))
        self._section("Jobs").set_rows(jobs)

    def apply_width(self, width: int) -> bool:
        """Whether the panel fits; the app owns the actual ``display`` flag."""
        return width >= NARROW_WIDTH
