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
        max_turns=40,
        tokens_used=100,
        max_tokens=2000,
        wall_seconds=10.0,
        max_wall_seconds=3600,
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


def test_settings_has_goal_pause_row():
    from zenagent.cli.tui.screens.editors import SettingsPicker

    settings.set_pref("goal_pause_on_session_switch", True)
    picker = SettingsPicker("off", "m", "auto", "auto")
    row = next(item for item in picker.items if item.value == "goal-pause")
    assert row.note == t("tui.settings.goal_pause_on")


def test_settings_goal_pause_row_reads_off():
    from zenagent.cli.tui.screens.editors import SettingsPicker

    settings.set_pref("goal_pause_on_session_switch", False)
    picker = SettingsPicker("off", "m", "auto", "auto")
    row = next(item for item in picker.items if item.value == "goal-pause")
    assert row.note == t("tui.settings.goal_pause_off")
