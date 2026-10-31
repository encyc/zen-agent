"""Todo HUD: one progress row in the dock, directly above the status band.

ZenAgent has no transcript-side todo surface, so the only place a long run's
phase/pointer shows up is this strip (omp calls the same thing its todo HUD).
It is intentionally minimal: one :class:`~textual.widgets.Static` row, hidden
whenever the session has no list, rendered from the canonical phase snapshot in
``ChatController.todos`` / ``RunStatus.todos`` — the same dicts the ``todo``
tool reports in ``details["phases"]``.

Repainting goes through the app's one status path (``_refresh_status``), so a
streamed delta cannot leave the HUD stale.
"""

from __future__ import annotations

from collections.abc import Callable

from rich.text import Text
from textual.widgets import Static

from zenagent.cli.tui import palette
from zenagent.tools.todo import summarize_todo

__all__ = ["MAX_CURRENT_CHARS", "TodoHud"]

_ACCENT = palette.ACCENT
_MUTED = palette.MUTED
_DIM = palette.DIM

#: The optional "what am I doing" fragment is clipped, never wrapped.
MAX_CURRENT_CHARS = 48


class TodoHud(Static):
    """``todo · <phase> · <current> · <closed>/<total> closed``; hidden if empty."""

    DEFAULT_CSS = """
    TodoHud {
        height: 1;
        background: #141c24;
        padding: 0 1;
    }
    """

    def __init__(self) -> None:
        super().__init__("", id="todo-hud")
        self._phases: list[dict] = []
        self._plain = ""
        self._on_click_handler: Callable[[], None] | None = None
        self.display = False

    @property
    def plain(self) -> str:
        """The row's text without colour markup (tests and click handling)."""
        return self._plain

    @property
    def phases(self) -> list[dict]:
        return list(self._phases)

    def set_handler(self, handler: Callable[[], None] | None) -> None:
        """Install the click handler (None clears it)."""
        self._on_click_handler = handler

    def on_click(self) -> None:
        if self._on_click_handler is not None:
            self._on_click_handler()

    def set_todos(self, phases: list[dict] | None) -> None:
        """Render one canonical phase snapshot; empty means "hide the row"."""
        self._phases = list(phases or [])
        info = summarize_todo(self._phases)
        if info["total"] == 0:
            self._plain = ""
            self.update("")
            self.display = False
            return

        text = Text()
        text.append("todo", style=_ACCENT)
        if info["phase"]:
            text.append(" · ", style=_DIM)
            text.append(info["phase"], style=_MUTED)
        if info["current"]:
            current = info["current"]
            if len(current) > MAX_CURRENT_CHARS:
                current = current[: MAX_CURRENT_CHARS - 1] + "…"
            text.append(" · ", style=_DIM)
            text.append(current, style=_MUTED)
        text.append(" · ", style=_DIM)
        text.append(f"{info['closed']}/{info['total']} closed", style=_MUTED)
        if info["remaining"] == 0:
            text.append(" · ", style=_DIM)
            text.append("all done", style=palette.OK)
        elif info["blocked"]:
            text.append(" · ", style=_DIM)
            text.append(f"{info['blocked']} blocked", style=_DIM)

        self._plain = text.plain
        self.update(text)
        self.display = True
