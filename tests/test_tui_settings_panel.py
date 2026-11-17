"""Settings panel: the full-screen tabbed page (omp-style).

The panel is a modal on the screen stack, so queries go through
``app.screen`` (the repo-wide idiom, see tests/test_tui_approval.py).
The activation handler is stubbed, so no test opens sub-pickers.
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


async def test_enter_activates_and_repaints_live_values(fake_runtime):
    app = ZenAgentApp(fake_runtime)
    activated: list[str] = []

    async def on_action(value: str) -> None:
        activated.append(value)

    async with app.run_test(size=(120, 40)) as pilot:
        app.open_menu("settings", lambda: SettingsPanel(app, on_action), lambda _r: None)
        await pilot.pause()
        await pilot.press("enter")  # the items list owns focus from the start
        await pilot.pause()
        await pilot.pause()
        assert activated == ["theme"]
        assert isinstance(app.screen, SettingsPanel)


async def test_description_line_follows_the_highlight(fake_runtime):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await app._dispatch_command("settings", "")
        await pilot.pause()
        panel = app.screen
        description = _desc_text(panel)
        assert "theme" in description.lower() or "主题" in description
        await pilot.press("down")
        await pilot.pause()
        description = _desc_text(panel)
        assert "language" in description.lower() or "语言" in description


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
