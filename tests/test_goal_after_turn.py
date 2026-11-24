from zenagent.agent.session import Session
from zenagent.app.goal import (
    GoalController,
    GoalLimits,
    GoalTurnContext,
    GoalVerdict,
)


class FakeVerifier:
    def __init__(self, *verdicts):
        self.verdicts = list(verdicts)

    async def verify(self, goal, ctx):
        if not self.verdicts:
            return GoalVerdict("not_met", "still failing")
        return self.verdicts.pop(0)


def _ctl(tmp_path, verifier, *, check=None, **limits):
    session = Session(tmp_path / "s.jsonl")
    session.materialize()
    ctl = GoalController(
        limits=GoalLimits(**limits),
        verifier=verifier,
        check_verifier=verifier if check else None,
        now=lambda: 100.0,
    )
    ctl.bind_session(session)
    ctl.set("fix tests", check=check)
    return ctl


def _ctx(**overrides):
    data = {
        "messages": [],
        "auto": True,
        "tool_calls": 1,
        "termination": "stop",
        "tokens_delta": 10,
        "subagent_tokens_delta": 0,
        "elapsed_s": 1.0,
        "user_queued": False,
    }
    data.update(overrides)
    return GoalTurnContext(**data)


async def test_met_completes(tmp_path):
    ctl = _ctl(tmp_path, FakeVerifier(GoalVerdict("met", "green")))
    decision = await ctl.after_turn(_ctx())
    assert decision.action == "stop"
    assert decision.status == "complete"


async def test_no_usage_ever_stops_the_goal(tmp_path):
    """Only a verdict, a blocker or the user stops a goal now."""
    ctl = _ctl(tmp_path, FakeVerifier(GoalVerdict("not_met", "still failing")))
    decision = await ctl.after_turn(_ctx(tokens_delta=10**9, elapsed_s=10**9))

    assert decision.action == "continue"
    assert ctl.state is not None and ctl.state.status == "active"
    assert ctl.state.tokens_used >= 10**9


async def test_not_met_continues_with_budget_and_reason(tmp_path):
    ctl = _ctl(tmp_path, FakeVerifier(GoalVerdict("not_met", "3 tests failed")))
    decision = await ctl.after_turn(_ctx())
    assert decision.action == "continue"
    assert "fix tests" in decision.prompt
    assert "3 tests failed" in decision.prompt


async def test_a_runaway_interrupt_pauses_the_goal(tmp_path):
    """A guard kill is recoverable: the goal pauses, it does not get blocked."""
    ctl = _ctl(tmp_path, FakeVerifier(GoalVerdict("not_met", "nope")))
    decision = await ctl.after_turn(_ctx(termination="loop_detected"))
    assert decision.action == "stop"
    assert decision.status == "paused"


async def test_three_idle_turns_block(tmp_path):
    ctl = _ctl(tmp_path, FakeVerifier(GoalVerdict("not_met", "nope")))
    for _ in range(2):
        assert (await ctl.after_turn(_ctx(tool_calls=0))).status == "active"
    decision = await ctl.after_turn(_ctx(tool_calls=0))
    assert decision.status == "blocked"


async def test_user_turn_resets_no_progress(tmp_path):
    ctl = _ctl(tmp_path, FakeVerifier(GoalVerdict("not_met", "nope")))
    await ctl.after_turn(_ctx(tool_calls=0))
    await ctl.after_turn(_ctx(tool_calls=0))
    await ctl.after_turn(_ctx(auto=False, tool_calls=0))
    decision = await ctl.after_turn(_ctx(tool_calls=0))
    assert decision.status == "active"


async def test_three_identical_check_failures_block(tmp_path):
    ctl = _ctl(
        tmp_path,
        FakeVerifier(*[GoalVerdict("not_met", "exit 127: no tool")] * 3),
        check="pytest -q",
    )
    await ctl.after_turn(_ctx())
    await ctl.after_turn(_ctx())
    decision = await ctl.after_turn(_ctx())
    assert decision.status == "blocked"


async def test_verifier_tokens_count_against_budget(tmp_path):
    ctl = _ctl(
        tmp_path,
        FakeVerifier(GoalVerdict("not_met", "nope", tokens=7)),
    )
    await ctl.after_turn(_ctx(tokens_delta=1))
    assert ctl.state.tokens_used == 8


async def test_subagent_tokens_are_counted_but_never_stop_the_goal(tmp_path):
    ctl = _ctl(tmp_path, FakeVerifier(GoalVerdict("not_met", "nope")))
    decision = await ctl.after_turn(_ctx(tokens_delta=1, subagent_tokens_delta=6))
    assert decision.action == "continue"
    assert ctl.state is not None and ctl.state.tokens_used == 7
