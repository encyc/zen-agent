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


async def test_not_met_continues_with_budget_and_reason(tmp_path):
    ctl = _ctl(tmp_path, FakeVerifier(GoalVerdict("not_met", "3 tests failed")))
    decision = await ctl.after_turn(_ctx())
    assert decision.action == "continue"
    assert "fix tests" in decision.prompt
    assert "3 tests failed" in decision.prompt


async def test_turn_budget_wraps_up_once(tmp_path):
    ctl = _ctl(tmp_path, FakeVerifier(GoalVerdict("not_met", "nope")), max_turns=1)
    first = await ctl.after_turn(_ctx())
    assert first.action == "continue" and first.wrap_up is True
    second = await ctl.after_turn(_ctx())
    assert second.action == "stop" and second.status == "budget_limited"


async def test_token_budget_hard_stops_without_wrap_up(tmp_path):
    ctl = _ctl(
        tmp_path,
        FakeVerifier(GoalVerdict("not_met", "nope")),
        max_tokens=5,
    )
    decision = await ctl.after_turn(_ctx(tokens_delta=6))
    assert decision.action == "stop"
    assert decision.status == "budget_limited"
    assert decision.wrap_up is False


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


async def test_budget_counts_subagents(tmp_path):
    ctl = _ctl(
        tmp_path,
        FakeVerifier(GoalVerdict("not_met", "nope")),
        max_tokens=5,
    )
    decision = await ctl.after_turn(_ctx(tokens_delta=1, subagent_tokens_delta=6))
    assert decision.status == "budget_limited"
