"""Settings panel: the rail + items dialog.

The panel is a modal on the screen stack, so queries go through
``app.screen`` (the repo-wide idiom, see tests/test_tui_approval.py).
The activation handler is stubbed, so no test opens sub-pickers.
"""

from textual.widgets import OptionList

from zenagent.cli.tui.app import ZenAgentApp
from zenagent.cli.tui.screens.editors import SettingsPanel


async def test_panel_shows_rail_and_first_section(fake_runtime):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await app._dispatch_command("settings", "")
        await pilot.pause()
        panel = app.screen
        rail = list(panel.query_one("#settings-rail", OptionList).options)
        assert len(rail) == 4
        items = panel.items_for("appearance")
        assert [item.value for item in items] == ["theme", "language"]


async def test_rail_selection_switches_the_items_pane(fake_runtime):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await app._dispatch_command("settings", "")
        await pilot.pause()
        panel = app.screen
        panel.query_one("#settings-rail", OptionList).highlighted = 2  # search
        await pilot.press("enter")
        await pilot.pause()
        rows = list(panel.query_one("#settings-items", OptionList).options)
        text = " ".join(str(option.prompt) for option in rows)
        assert "search" in text.lower() or "搜索" in text


async def test_item_activation_calls_the_action_handler(fake_runtime):
    app = ZenAgentApp(fake_runtime)
    activated: list[str] = []

    async def on_action(value: str) -> None:
        activated.append(value)

    async with app.run_test(size=(120, 40)) as pilot:
        app.open_menu("settings", lambda: SettingsPanel(app, on_action), lambda _r: None)
        await pilot.pause()
        panel = app.screen
        await pilot.press("right")  # rail → items pane
        await pilot.pause()
        panel.query_one("#settings-items", OptionList).highlighted = 0  # theme
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        assert activated == ["theme"]
        # The panel stays open (a sub-picker lands on top of it in real use).
        assert isinstance(app.screen, SettingsPanel)


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
