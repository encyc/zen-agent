"""Settings panel: the full-screen tabbed page (omp-style) with dropdown edits.

The panel is a modal on the screen stack, so queries go through
``app.screen`` (the repo-wide idiom, see tests/test_tui_approval.py).
Enter on a value row opens the option dropdown in place; the tests drive
it with pilot presses and read the panel's own state.
"""

from textual.widgets import OptionList

from zenagent.cli.tui.app import ZenAgentApp
from zenagent.cli.tui.screens.editors import SettingsPanel


def _tabs_text(panel: SettingsPanel) -> str:
    return str(panel._tabs_line)


def _desc_text(panel: SettingsPanel) -> str:
    return panel._description


def _item_prompts(panel: SettingsPanel) -> list[str]:
    options = panel.query_one("#settings-items", OptionList).options
    return [str(option.prompt) for option in options]


async def test_panel_opens_full_screen_with_four_sections(fake_runtime):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await app._dispatch_command("settings", "")
        await pilot.pause()
        panel = app.screen
        assert isinstance(panel, SettingsPanel)
        # Full screen: the panel covers the whole terminal, not a fixed box.
        assert panel.outer_size.width == 120 and panel.outer_size.height == 40
        tabs = _tabs_text(panel)
        for title in ("Appearance", "Model", "Search", "Session"):
            assert title in tabs
        # First section's rows, label and value as two columns.
        rows = _item_prompts(panel)
        assert len(rows) == 2
        assert "Theme" in rows[0]


async def test_left_right_switches_sections_instantly(fake_runtime):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await app._dispatch_command("settings", "")
        await pilot.pause()
        panel = app.screen
        await pilot.press("right")
        await pilot.pause()
        assert "Model" in _tabs_text(panel)
        rows = _item_prompts(panel)
        assert any("thinking" in row.lower() or "推理" in row for row in rows)
        await pilot.press("left")
        await pilot.press("left")
        await pilot.pause()
        # Wraps to the last section.
        assert "Session" in _tabs_text(panel)


async def test_enter_opens_dropdown_with_options_and_current_marked(
    fake_runtime,
):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await app._dispatch_command("settings", "")
        await pilot.pause()
        panel = app.screen
        await pilot.press("enter")  # the items list owns focus from the start
        await pilot.pause()
        assert isinstance(app.screen, SettingsPanel)  # no palette took over
        dropdown = panel.query_one("#settings-dropdown-list", OptionList)
        assert panel.query_one("#settings-dropdown").display
        prompts = [str(option.prompt) for option in dropdown.options]
        assert len(prompts) == 3
        assert any("auto" in prompt and "●" in prompt for prompt in prompts)


async def test_dropdown_selection_applies_and_closes(fake_runtime):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await app._dispatch_command("settings", "")
        await pilot.pause()
        panel = app.screen
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("down")  # auto → dark
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        assert app._theme_spec == "dark"
        assert not panel.query_one("#settings-dropdown").display
        row_note = panel.items_for("appearance")[0].note
        assert row_note == "dark"


async def test_dropdown_escape_cancels_without_applying(fake_runtime):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await app._dispatch_command("settings", "")
        await pilot.pause()
        panel = app.screen
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("down")
        await pilot.press("escape")
        await pilot.pause()
        assert app._theme_spec == "auto"  # untouched
        assert not panel.query_one("#settings-dropdown").display


async def test_goal_pause_dropdown_flips_the_pref(fake_runtime):
    from zenagent import settings

    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await app._dispatch_command("settings", "")
        await pilot.pause()
        panel = app.screen
        panel._show_section(3)  # session
        await pilot.pause()
        panel.query_one("#settings-items", OptionList).highlighted = 0  # goal-pause
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("up")  # current "off" is highlighted; move to "on"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        assert settings.get_pref("goal_pause_on_session_switch") is True


async def test_search_provider_dropdown_keeps_the_page_up(fake_runtime):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await app._dispatch_command("settings", "")
        await pilot.pause()
        panel = app.screen
        await pilot.press("right")  # model section
        await pilot.pause()
        await pilot.press("right")  # search section
        await pilot.pause()
        await pilot.press("enter")  # search-provider row
        await pilot.pause()
        # The dropdown opens in place: still our page, no palette takeover.
        assert isinstance(app.screen, SettingsPanel)
        assert panel.query_one("#settings-dropdown").display
        prompts = [
            str(option.prompt)
            for option in panel.query_one(
                "#settings-dropdown-list", OptionList
            ).options
        ]
        assert len(prompts) >= 4  # auto + the keyed providers


async def test_overlay_action_hides_the_page_until_resume(fake_runtime):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await app._dispatch_command("settings", "")
        await pilot.pause()
        panel = app.screen
        await pilot.press("right")  # model section
        await pilot.pause()
        panel.query_one("#settings-items", OptionList).highlighted = 2  # model-keys
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        # The page hid itself while the key picker is up...
        assert panel.display is False
        assert app.screen is not panel
        # ...and comes back when the picker closes.
        await pilot.press("escape")
        await pilot.pause()
        await pilot.pause()
        assert panel.display is True
        assert app.screen is panel


async def test_goal_pause_row_reflects_the_pref_live(fake_runtime):
    from zenagent import settings
    from zenagent.i18n import t

    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await app._dispatch_command("settings", "")
        await pilot.pause()
        panel = app.screen
        settings.set_pref("goal_pause_on_session_switch", True)
        row = next(item for item in panel.items if item.value == "goal-pause")
        assert row.note == t("tui.settings.goal_pause_on")
