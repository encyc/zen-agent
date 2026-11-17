"""Goal panel: HUD click opens it, actions go through the /goal command path.

Modal screens sit on top of the screen stack, so queries go through
``app.screen`` (the repo-wide idiom, see tests/test_tui_approval.py).
The real goal controller stays wired (its tick runs on the app); only its
``snapshot()`` is patched to return the view under test, and the dispatch is
stubbed, so no test runs a real goal loop.
"""

from zenagent.app.views import GoalView
from zenagent.cli.tui.app import ZenAgentApp
from zenagent.cli.tui.widgets.goal_hud import GoalHud


def _view(status="active") -> GoalView:
    return GoalView(
        objective="fix the flaky tests",
        status=status,
        check="pytest -x",
        turns_used=3,
        max_turns=40,
        tokens_used=1200,
        max_tokens=0,
        wall_seconds=95.0,
        max_wall_seconds=0,
        last_verdict="pass",
        last_reason="tests green",
        updated_at=1,
    )


def _patch_snapshot(app, monkeypatch, *, status="active") -> None:
    goal = getattr(app.runtime, "goal", None)
    assert goal is not None, "prepare should wire the goal controller"
    monkeypatch.setattr(goal, "snapshot", lambda: _view(status))


async def test_hud_click_opens_panel_with_full_fields(fake_runtime, monkeypatch):
    app = ZenAgentApp(fake_runtime)
    _patch_snapshot(app, monkeypatch)
    async with app.run_test(size=(120, 40)) as pilot:
        hud = app.query_one(GoalHud)
        hud.set_goal(app.chat.status.goal)
        hud.on_click()  # the handler on_mount installed is open_goal_panel
        await pilot.pause()
        await pilot.pause()
        text = app.screen._body()
        assert "fix the flaky tests" in text
        assert "pytest -x" in text
        assert "3/40" in text
        assert "pass" in text


async def test_toggle_dispatches_pause_for_an_active_goal(fake_runtime, monkeypatch):
    app = ZenAgentApp(fake_runtime)
    _patch_snapshot(app, monkeypatch)
    calls: list[tuple[str, str]] = []

    async def fake_dispatch(name, argument):
        calls.append((name, argument))

    monkeypatch.setattr(app, "_dispatch_command", fake_dispatch)
    async with app.run_test(size=(120, 40)) as pilot:
        app.open_goal_panel()
        await pilot.pause()
        await pilot.press("p")
        await pilot.pause()
        await pilot.pause()
        assert calls == [("goal", "pause")]


async def test_toggle_dispatches_resume_for_a_paused_goal(fake_runtime, monkeypatch):
    app = ZenAgentApp(fake_runtime)
    _patch_snapshot(app, monkeypatch, status="paused")
    calls: list[tuple[str, str]] = []

    async def fake_dispatch(name, argument):
        calls.append((name, argument))

    monkeypatch.setattr(app, "_dispatch_command", fake_dispatch)
    async with app.run_test(size=(120, 40)) as pilot:
        app.open_goal_panel()
        await pilot.pause()
        await pilot.press("p")
        await pilot.pause()
        await pilot.pause()
        assert calls == [("goal", "resume")]


async def test_clear_requires_confirm(fake_runtime, monkeypatch):
    app = ZenAgentApp(fake_runtime)
    _patch_snapshot(app, monkeypatch)
    calls: list[tuple[str, str]] = []

    async def fake_dispatch(name, argument):
        calls.append((name, argument))

    monkeypatch.setattr(app, "_dispatch_command", fake_dispatch)
    async with app.run_test(size=(120, 40)) as pilot:
        app.open_goal_panel()
        await pilot.pause()
        await pilot.press("c")
        await pilot.pause()
        assert calls == []  # confirm still up
        await pilot.press("y")
        await pilot.pause()
        await pilot.pause()
        assert calls == [("goal", "clear")]


async def test_panel_without_goal_shows_the_empty_note(fake_runtime, monkeypatch):
    app = ZenAgentApp(fake_runtime)
    goal = getattr(app.runtime, "goal", None)
    if goal is not None:
        monkeypatch.setattr(goal, "snapshot", lambda: None)
    async with app.run_test(size=(120, 40)) as pilot:
        app.open_goal_panel()
        await pilot.pause()
        assert "no goal" in app.screen._body()
