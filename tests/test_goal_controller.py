from zenagent.agent.session import Session
from zenagent.app.goal import GoalController, GoalLimits


def _session(tmp_path):
    session = Session(tmp_path / "s.jsonl")
    session.materialize()
    return session


def test_set_persists_and_replays(tmp_path):
    session = _session(tmp_path)
    ctl = GoalController(limits=GoalLimits(max_turns=5), now=lambda: 100.0)
    ctl.bind_session(session)
    state = ctl.set("fix tests", check="pytest -q")
    assert state.status == "active"
    assert state.max_turns == 5

    again = GoalController(limits=GoalLimits(max_turns=5))
    again.bind_session(Session(session.path))
    view = again.snapshot()
    assert view is not None
    assert view.objective == "fix tests"
    assert view.check == "pytest -q"


def test_pause_resume_clear(tmp_path):
    ctl = GoalController(limits=GoalLimits(), now=lambda: 100.0)
    ctl.bind_session(_session(tmp_path))
    ctl.set("fix")
    assert ctl.pause().status == "paused"
    assert ctl.resume().status == "active"
    ctl.clear()
    assert ctl.snapshot() is None


def test_new_goal_replaces_old_id(tmp_path):
    ctl = GoalController(limits=GoalLimits(), now=lambda: 100.0)
    ctl.bind_session(_session(tmp_path))
    first = ctl.set("one")
    second = ctl.set("two")
    assert first.goal_id != second.goal_id
    assert ctl.snapshot().objective == "two"


def test_terminal_goal_cannot_resume(tmp_path):
    ctl = GoalController(limits=GoalLimits(), now=lambda: 100.0)
    ctl.bind_session(_session(tmp_path))
    ctl.set("one")
    ctl._state.status = "complete"
    assert ctl.resume() is None


def test_bind_session_loads_last_record(tmp_path):
    source = _session(tmp_path)
    ctl = GoalController(limits=GoalLimits(), now=lambda: 100.0)
    ctl.bind_session(source)
    ctl.set("first")
    ctl.set("second")
    fresh = GoalController(limits=GoalLimits())
    fresh.bind_session(Session(source.path))
    assert fresh.snapshot().objective == "second"
