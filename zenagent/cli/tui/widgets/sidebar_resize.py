"""A one-column grab strip that resizes the sidebar.

This module owns both halves of the gesture: the mouse handling widget, and the
``SidebarResize`` state object holding the width and its clamp rules, so the
math stays testable without synthesising move events. The app keeps one-line
delegates (``sidebar_width``/``resize_sidebar``/...) — tests address them there.
"""

from __future__ import annotations

from textual.widgets import Static

from zenagent.cli.tui.widgets.sidebar import NARROW_WIDTH, Sidebar

__all__ = ["SidebarResize", "SidebarResizeHandle"]


class SidebarResizeHandle(Static):
    """1-column grab area between the transcript column and the sidebar."""

    DEFAULT_CSS = """
    SidebarResizeHandle {
        width: 1;
        height: 1fr;
        background: $surface;
    }
    SidebarResizeHandle:hover {
        background: $line;
    }
    """

    def __init__(self) -> None:
        super().__init__("", id="sidebar-resize")
        self.display = False

    def on_mouse_down(self, event) -> None:
        """Start a drag: capture the mouse so moves keep arriving here."""
        self.capture_mouse()
        self.app.begin_sidebar_resize()
        event.stop()

    def on_mouse_move(self, event) -> None:
        if self.app.mouse_captured is self:
            self.app.resize_sidebar(event.screen_x)
            event.stop()

    def on_mouse_up(self, event) -> None:
        self.release_mouse()
        self.app.end_sidebar_resize()
        event.stop()


class SidebarResize:
    """Sidebar width state and the drag math.

    Every computation needs the app's live size and repaint pipeline, so the
    object holds an app reference; the width constants stay on ``ZenAgentApp``
    (tests reference them there).
    """

    def __init__(self, app) -> None:
        self._app = app
        self.resizing = False
        self.width = app.DEFAULT_SIDEBAR_WIDTH

    @property
    def band_width(self) -> int:
        """Width the dock actually has: the terminal minus the sidebar chrome.

        Segment degradation must use this, not ``size.width``: with the sidebar
        open the dock is narrower, and dragging changes it.
        """
        app = self._app
        if app._sidebar_open and app.size.width >= NARROW_WIDTH:
            return max(20, app.size.width - self.width - 1)
        return app.size.width

    def begin(self) -> None:
        """Mouse-down on the handle; the move handler owns the actual updates."""
        self.resizing = True

    def end(self) -> None:
        self.resizing = False

    def resize(self, screen_x: int) -> None:
        """Anchor the sidebar's left edge at ``screen_x`` (the right edge is fixed)."""
        app = self._app
        width = app.size.width - int(screen_x)
        room = app.size.width - app.MAIN_COLUMN_MIN
        self.width = max(
            app.MIN_SIDEBAR_WIDTH,
            min(app.MAX_SIDEBAR_WIDTH, room, width),
        )
        self.apply()
        # Dragging changes the dock column: re-degrade the status segments on
        # the next coalesced repaint instead of leaving the old layout on screen.
        app._on_change()

    def apply(self) -> None:
        app = self._app
        for sidebar in app.query(Sidebar):
            sidebar.styles.width = self.width
