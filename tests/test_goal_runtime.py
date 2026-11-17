import pytest

from zenagent import settings
from zenagent.app.goal import GoalController, GoalLimits, GoalVerdict


@pytest.fixture(autouse=True)
def _reset_goal_pref(monkeypatch, tmp_path):
    monkeypatch.setenv("ZENAGENT_SETTINGS_PATH", str(tmp_path / "settings.json"))
    settings.clear_cache()
    settings.set_pref("goal_pause_on_session_switch", None)
    yield
    settings.set_pref("goal_pause_on_session_switch", None)
    settings.clear_cache()


class FakeVerifier:
    async def verify(self, goal, ctx):
        return GoalVerdict("not_met", "again")


async def test_switch_keeps_goal_active_by_default(runtime_factory, tmp_path):
    settings.set_pref("goal_pause_on_session_switch", False)
    runtime = await runtime_factory()
    original = runtime.session.path
    runtime.goal = GoalController(limits=GoalLimits(), verifier=FakeVerifier())
    runtime.goal.bind_session(runtime.session)
    runtime.goal.set("fix")

    other = tmp_path / "other.jsonl"
    other.write_text("", encoding="utf-8")
    await runtime.switch_session(other)
    assert runtime.goal.snapshot() is None

    await runtime.switch_session(original)
    assert runtime.goal.snapshot().status == "active"


async def test_switch_pause_pref_pauses(runtime_factory, tmp_path):
    settings.set_pref("goal_pause_on_session_switch", True)
    runtime = await runtime_factory()
    original = runtime.session.path
    runtime.goal = GoalController(limits=GoalLimits(), verifier=FakeVerifier())
    runtime.goal.bind_session(runtime.session)
    runtime.goal.set("fix")

    other = tmp_path / "other.jsonl"
    other.write_text("", encoding="utf-8")
    await runtime.switch_session(other)
    assert runtime.goal.snapshot() is None
    # Switch back: the old session's goal was persisted as paused.
    await runtime.switch_session(original)
    assert runtime.goal.snapshot().status == "paused"


async def test_check_goal_stays_active_on_switch_when_set_here(
    runtime_factory, tmp_path
):
    settings.set_pref("goal_pause_on_session_switch", False)
    runtime = await runtime_factory()
    original = runtime.session.path
    runtime.goal = GoalController(limits=GoalLimits(), verifier=FakeVerifier())
    runtime.goal.bind_session(runtime.session)
    runtime.goal.set("fix", check="echo ok")

    other = tmp_path / "other-check.jsonl"
    other.write_text("", encoding="utf-8")
    await runtime.switch_session(other)
    await runtime.switch_session(original)

    assert runtime.goal.snapshot().status == "active"


async def test_persisted_check_restores_paused(runtime_factory):
    runtime = await runtime_factory()
    runtime.goal = GoalController(limits=GoalLimits(), verifier=FakeVerifier())
    runtime.goal.bind_session(runtime.session)
    runtime.goal.set("fix", check="echo ok")
    # Simulate a fresh controller reading the same file.
    fresh = GoalController(limits=GoalLimits(), verifier=FakeVerifier())
    fresh.bind_session(runtime.session)
    assert fresh.snapshot().status == "paused"
