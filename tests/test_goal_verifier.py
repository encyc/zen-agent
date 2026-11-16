import sys

import pytest

from zenagent.app.goal import GoalLimits, GoalState, GoalTurnContext
from zenagent.app.goal_verifier import CheckVerifier, GoalVerifierError, ModelVerifier
from zenagent.types import Usage


def _goal(check=None):
    return GoalState.new("fix tests", check=check, limits=GoalLimits())


def _ctx():
    return GoalTurnContext(
        messages=[],
        auto=True,
        tool_calls=1,
        termination="stop",
        tokens_delta=0,
        subagent_tokens_delta=0,
        elapsed_s=1.0,
        user_queued=False,
    )


async def test_check_verifier_met_and_not_met(tmp_path):
    verifier = CheckVerifier(timeout_s=5, cwd=tmp_path)
    met = await verifier.verify(
        _goal(f'{sys.executable} -c "print(1)"'), _ctx()
    )
    assert met.verdict == "met"
    failed = await verifier.verify(
        _goal(f'{sys.executable} -c "raise SystemExit(2)"'), _ctx()
    )
    assert failed.verdict == "not_met"
    assert failed.evidence


async def test_check_verifier_timeout_is_not_met(tmp_path):
    verifier = CheckVerifier(timeout_s=0.1, cwd=tmp_path)
    verdict = await verifier.verify(
        _goal(f'{sys.executable} -c "import time; time.sleep(2)"'), _ctx()
    )
    assert verdict.verdict == "not_met"
    assert "timed out" in verdict.reason.lower()


async def test_model_verifier_parses_json(runtime_factory):
    async def stream(model, messages, tools, options):
        from zenagent.events import AssistantEvent
        from zenagent.types import AssistantMessage, TextContent

        partial = AssistantMessage(
            content=[TextContent(text='{"verdict":"met","reason":"tests pass"}')],
            provider="p",
            model="m",
            usage=Usage(input=10, output=5),
            stop_reason="stop",
            timestamp=0,
        )
        yield AssistantEvent(type="start", partial=partial)
        yield AssistantEvent(type="done", partial=partial, reason="stop")

    runtime = await runtime_factory(stream_fn=stream)
    verifier = ModelVerifier(
        stream_fn=runtime.agent.stream_fn,
        model=runtime.agent.model,
        api_key="test-key",
        max_tokens=64,
    )
    verdict = await verifier.verify(_goal(), _ctx())
    assert verdict.verdict == "met"
    assert verdict.tokens > 0


async def test_model_verifier_rejects_non_json(runtime_factory):
    async def stream(model, messages, tools, options):
        from zenagent.events import AssistantEvent
        from zenagent.types import AssistantMessage, TextContent

        partial = AssistantMessage(
            content=[TextContent(text="not json")],
            provider="p",
            model="m",
            usage=Usage(),
            stop_reason="stop",
            timestamp=0,
        )
        yield AssistantEvent(type="start", partial=partial)
        yield AssistantEvent(type="done", partial=partial, reason="stop")

    runtime = await runtime_factory(stream_fn=stream)
    verifier = ModelVerifier(
        stream_fn=runtime.agent.stream_fn,
        model=runtime.agent.model,
        api_key="test-key",
        max_tokens=64,
    )
    with pytest.raises(GoalVerifierError):
        await verifier.verify(_goal(), _ctx())
