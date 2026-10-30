"""Model/session pickers and the responsive sidebar."""

from zenagent.cli.tui.screens.modals import ModelPicker
from zenagent.cli.tui.widgets.sidebar import Sidebar


async def test_model_picker_switches_model(picker_app):
    app = picker_app
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.open_model_picker()
        await pilot.pause()
        assert isinstance(app.screen, ModelPicker)

        await pilot.press("down", "enter")
        await pilot.pause()
        assert app.runtime.agent.model.id != "deepseek-flash"


async def test_sidebar_hidden_on_narrow_screen(narrow_app):
    app = narrow_app
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        assert app.query_one(Sidebar).display is False


async def test_session_picker_opens_and_closes(picker_app):
    from zenagent.cli.tui.screens.modals import SessionPicker

    app = picker_app
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.open_session_picker()
        await pilot.pause()
        assert isinstance(app.screen, SessionPicker)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, SessionPicker)


async def test_command_palette_opens(picker_app):
    from textual.command import CommandPalette

    app = picker_app
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+k")
        await pilot.pause()
        assert isinstance(app.screen, CommandPalette)


async def test_repeat_shortcut_focuses_instead_of_stacking(picker_app):
    """omp's single-instance menus: ctrl+p twice must not open two pickers."""
    app = picker_app
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("alt+p")
        await pilot.pause()
        first = app.screen
        assert isinstance(first, ModelPicker)

        await pilot.press("alt+p")
        await pilot.pause()
        assert app.screen is first
        assert len([s for s in app.screen_stack if isinstance(s, ModelPicker)]) == 1


async def test_picker_is_bottom_anchored_and_full_width(picker_app):
    app = picker_app
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.open_session_picker()
        await pilot.pause()
        panel = app.screen.query_one("#palette")
        assert panel.region.width == 120, "the palette spans the terminal"
        assert panel.region.y + panel.region.height == 40, "and sits on the bottom"


async def test_picker_search_filters_the_list(picker_app):
    app = picker_app
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.open_model_picker()
        await pilot.pause()
        screen = app.screen
        before = len(screen._filtered)

        for char in "deepseek":
            await pilot.press(char)
        await pilot.pause()
        assert len(screen._filtered) < before
        assert all("deepseek" in item.label for item in screen._filtered)
