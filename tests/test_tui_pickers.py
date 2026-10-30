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
        await pilot.press("ctrl+p")
        await pilot.pause()
        assert isinstance(app.screen, CommandPalette)
