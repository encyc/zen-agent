import pytest

from zenagent.app.goal import (
    GoalLimits,
    GoalState,
    goal_tokens,
    parse_goal_args,
    render_goal_prompt,
)
from zenagent.types import Usage


def test_parse_empty_is_status():
    assert parse_goal_args("").action == "status"
    assert parse_goal_args("   ").action == "status"


def test_parse_lifecycle_commands():
    assert parse_goal_args("pause").action == "pause"
    assert parse_goal_args("resume").action == "resume"
    assert parse_goal_args("clear").action == "clear"
    assert parse_goal_args("stop").action == "clear"


def test_parse_check_and_budget_before_objective():
    args = parse_goal_args('--check "pytest -q" --budget 7 fix flaky tests')
    assert args.action == "set"
    assert args.check == "pytest -q"
    assert args.max_turns == 7
    assert args.objective == "fix flaky tests"


def test_flags_after_objective_are_literal():
    args = parse_goal_args("fix tests --check nope")
    assert args.check is None
    assert args.objective == "fix tests --check nope"


@pytest.mark.parametrize(
    "raw", ["--budget -1 fix", "--budget x fix", "--check", "--check '' fix"]
)
def test_parse_rejects_bad_input(raw):
    with pytest.raises(ValueError):
        parse_goal_args(raw)


def test_budget_zero_means_no_ceiling():
    args = parse_goal_args("--budget 0 fix tests")
    assert args.max_turns == 0

    state = GoalState.new(
        "fix tests",
        max_turns=0,
        limits=GoalLimits(max_tokens=0, max_wall_seconds=0),
    )
    assert state.max_turns == 0
    assert "unlimited" in render_goal_prompt(state)


def test_state_roundtrip_and_defaults():
    state = GoalState.new("fix tests", check="pytest -q", limits=GoalLimits())
    assert state.status == "active"
    assert state.max_turns == 40
    assert GoalState.from_dict(state.to_dict()) == state


def test_goal_message_title_uses_objective():
    from zenagent.agent.session import _title_from_messages
    from zenagent.types import UserMessage

    text = "[goal] Continue...\n\n<objective>\nfix flaky tests\n</objective>\n"
    title = _title_from_messages(
        [UserMessage(content=text, timestamp=1, goal=True)]
    )
    assert "fix flaky tests" in title
    assert "Continue" not in title


def test_goal_tokens_is_provider_aware():
    usage = Usage(input=100, output=20, cache_read=60, cache_write=10)
    assert goal_tokens(usage, "openai-completions") == 120
    assert goal_tokens(usage, "anthropic-messages") == 190
