import pytest

from zenagent import settings
from zenagent.app.views import GoalView
from zenagent.cli.tui.widgets.goal_hud import GoalHud
from zenagent.i18n import t


@pytest.fixture(autouse=True)
def _reset_goal_pref(monkeypatch, tmp_path):
    monkeypatch.setenv("ZENAGENT_SETTINGS_PATH", str(tmp_path / "settings.json"))
    settings.clear_cache()
    yield
    settings.clear_cache()


def _view() -> GoalView:
    return GoalView(
        objective="fix tests",
        status="active",
        check="pytest -q",
        turns_used=3,
        tokens_used=100,
        wall_seconds=10.0,
        last_verdict="not_met",
        last_reason="2 failed",
        updated_at=1,
    )


def test_goal_hud_hides_without_goal():
    hud = GoalHud()
    hud.set_goal(None)
    assert hud.display is False


def test_goal_hud_shows_status_and_budget():
    hud = GoalHud()
    hud.set_goal(_view())
    assert hud.display is True
    assert "fix tests" in hud.render_text()


def test_goal_hud_reports_usage_without_a_ceiling():
    hud = GoalHud()
    hud.set_goal(_view())
    text = hud.render_text()
    assert "turns" in text and "∞" not in text


def test_goal_message_renders_compactly():
    from zenagent.cli.tui.widgets.rows import UserBlock

    block = UserBlock(
        "[goal] Continue...\n\n<objective>\nfix tests\n</objective>\n",
        goal=True,
    )
    assert "fix tests" in block.plain
    assert "Continue" not in block.plain


async def test_settings_has_goal_pause_row(fake_runtime):
    from zenagent.cli.tui.app import ZenAgentApp
    from zenagent.cli.tui.screens.editors import SettingsPanel

    settings.set_pref("goal_pause_on_session_switch", True)
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)):
        panel = SettingsPanel(app)
        row = next(item for item in panel.items if item.value == "goal-pause")
        assert row.note == t("tui.settings.goal_pause_on")


async def test_settings_goal_pause_row_reads_off(fake_runtime):
    from zenagent.cli.tui.app import ZenAgentApp
    from zenagent.cli.tui.screens.editors import SettingsPanel

    settings.set_pref("goal_pause_on_session_switch", False)
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)):
        panel = SettingsPanel(app)
        row = next(item for item in panel.items if item.value == "goal-pause")
        assert row.note == t("tui.settings.goal_pause_off")
