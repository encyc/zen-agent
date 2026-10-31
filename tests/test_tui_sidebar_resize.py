"""Drag-resizable sidebar: one grab column, clamped width, quiet on narrow screens."""

from zenagent.cli.tui.app import ZenAgentApp
from zenagent.cli.tui.widgets.sidebar import Sidebar
from zenagent.cli.tui.widgets.sidebar_resize import SidebarResizeHandle


async def _open(app, pilot):
    await pilot.press("ctrl+b")
    await pilot.pause()


async def test_resize_clamps_width_and_keeps_room_for_the_transcript(fake_runtime):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await _open(app, pilot)

        app.resize_sidebar(30)  # would be 90 wide: clamped to MAX
        assert app.sidebar_width == app.MAX_SIDEBAR_WIDTH

        app.resize_sidebar(10)  # would be 110 wide: still clamped
        assert app.sidebar_width == app.MAX_SIDEBAR_WIDTH

        app.resize_sidebar(118)  # would be 2 wide: clamped to MIN
        assert app.sidebar_width == app.MIN_SIDEBAR_WIDTH
        assert app.size.width - app.sidebar_width >= app.MAIN_COLUMN_MIN

        await pilot.pause()
        assert app.query_one(Sidebar).outer_size.width == app.sidebar_width


async def test_the_handle_is_visible_only_with_the_sidebar(fake_runtime):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        handle = app.query_one(SidebarResizeHandle)
        assert handle.display is False

        await _open(app, pilot)
        assert handle.display is True

        await pilot.press("ctrl+b")
        await pilot.pause()
        assert handle.display is False


async def test_narrow_screens_keep_the_handle_hidden(fake_runtime):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(90, 30)) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+b")
        await pilot.pause()

        assert app.query_one(Sidebar).display is False
        assert app.query_one(SidebarResizeHandle).display is False


async def test_mouse_down_captures_and_mouse_up_releases(fake_runtime):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await _open(app, pilot)
        handle = app.query_one(SidebarResizeHandle)

        await pilot.mouse_down(handle)
        await pilot.pause()
        assert app.mouse_captured is handle

        await pilot.mouse_up(handle)
        await pilot.pause()
        assert app.mouse_captured is None
