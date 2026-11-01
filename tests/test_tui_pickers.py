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


async def test_ctrl_p_cycles_to_the_next_usable_model(picker_app):
    """The third entry point (cycling) rides the same switch helper."""
    app = picker_app
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+p")
        await pilot.pause()
        assert app.runtime.agent.model.id != "deepseek-flash"
        assert app.chat.status.model_id == app.runtime.agent.model.id


async def test_switching_from_the_composer_heals_the_missing_key_banner(picker_app):
    """A switch through `/model <id>` repeats every side effect of the picker."""
    from zenagent.cli.tui.widgets.error_banner import ErrorBanner
    from zenagent.cli.tui.widgets.prompt_input import PromptInput

    app = picker_app
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.runtime.missing_api_key = "DEEPSEEK_API_KEY"
        app.chat.set_last_error("no API key for deepseek/deepseek-flash")
        app._refresh_status()
        await pilot.pause()
        assert app.query_one(ErrorBanner).display is True

        prompt = app.query_one(PromptInput)
        prompt.focus()
        for char in "/model deepseek-v4-pro":
            await pilot.press(char)
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

        assert app.runtime.agent.model.id == "deepseek-v4-pro"
        assert app.runtime.missing_api_key is None
        assert app.query_one(ErrorBanner).display is False


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
