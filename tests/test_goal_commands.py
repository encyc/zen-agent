from zenagent.app.commands import build_default_commands


async def test_goal_set_status_pause_resume_clear(services, monkeypatch):
    # Command tests must not start a real goal run: stub the chat starters and
    # assert the controller state directly.
    monkeypatch.setattr(services.chat, "start_goal", lambda *a, **k: "goal started")
    monkeypatch.setattr(services.chat, "maybe_continue_goal", lambda: None)
    registry = build_default_commands(services)
    set_result = await registry.dispatch("goal", "fix flaky tests")
    assert set_result.message
    services.runtime.goal.set("fix flaky tests")

    status = await registry.dispatch("goal", "")
    assert "fix flaky tests" in status.message

    paused = await registry.dispatch("goal", "pause")
    assert "paused" in paused.message
    resumed = await registry.dispatch("goal", "resume")
    assert "active" in resumed.message
    cleared = await registry.dispatch("goal", "clear")
    assert "cleared" in cleared.message


async def test_goal_check_flag(services, monkeypatch):
    captured = {}

    def fake_start(objective, *, check=None):
        captured.update(objective=objective, check=check)
        return "goal started"

    monkeypatch.setattr(services.chat, "start_goal", fake_start)
    registry = build_default_commands(services)
    result = await registry.dispatch("goal", '--check "pytest -q" fix tests')
    assert result.message == "goal started"
    assert captured == {"objective": "fix tests", "check": "pytest -q"}


async def test_goal_budget_flag_is_rejected(services):
    registry = build_default_commands(services)
    result = await registry.dispatch("goal", "--budget 3 fix tests")
    assert "--budget" in result.message


async def test_goal_rejects_bad_arguments(services):
    registry = build_default_commands(services)
    result = await registry.dispatch("goal", "--budget nope fix")
    assert "usage" in result.message.lower() or "invalid" in result.message.lower()


def test_user_view_carries_goal_flag():
    from zenagent.app.views import user_view

    assert user_view("continue", goal=True).goal is True
