"""A one-column grab strip that resizes the sidebar.

The width decision lives on the app (``ZenApp.resize_sidebar``): this widget only
owns the mouse gesture, so the clamp rules stay testable without synthesising
move events.
"""

from __future__ import annotations

from textual.widgets import Static

__all__ = ["SidebarResizeHandle"]


class SidebarResizeHandle(Static):
    """1-column grab area between the transcript column and the sidebar."""

    DEFAULT_CSS = """
    SidebarResizeHandle {
        width: 1;
        height: 1fr;
        background: #141c24;
    }
    SidebarResizeHandle:hover {
        background: #263441;
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
