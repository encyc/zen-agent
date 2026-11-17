from pathlib import Path

from zenagent.app.goal import GoalLimits
from zenagent.limits import Limits, parse_limits


def test_goal_defaults():
    limits = Limits()
    assert limits.goal_max_turns == 40
    assert limits.goal_max_wall_minutes == 240
    assert limits.goal_max_tokens == 2_000_000
    assert limits.goal_check_timeout_s == 300
    assert limits.goal_verifier_max_tokens == 512


def test_parse_goal_table():
    limits = parse_limits(
        {
            "goal": {
                "max_turns": 5,
                "max_wall_minutes": 30,
                "max_tokens": 10000,
                "check_timeout_s": 60,
                "verifier_max_tokens": 128,
            }
        },
        Path("config.toml"),
    )
    assert limits.goal_max_turns == 5
    assert limits.goal_max_wall_minutes == 30
    assert limits.goal_max_tokens == 10000
    assert limits.goal_check_timeout_s == 60
    assert limits.goal_verifier_max_tokens == 128


def test_goal_limits_adapter():
    goal = GoalLimits.from_limits(Limits(goal_max_wall_minutes=2))
    assert goal.max_wall_seconds == 120


def test_goal_budgets_accept_zero_as_unlimited():
    limits = parse_limits(
        {
            "goal": {
                "max_turns": 0,
                "max_wall_minutes": 0,
                "max_tokens": 0,
            }
        },
        Path("config.toml"),
    )

    assert limits.goal_max_turns == 0
    assert limits.goal_max_wall_minutes == 0
    assert limits.goal_max_tokens == 0
    goal = GoalLimits.from_limits(limits)
    assert goal.max_turns == 0
    assert goal.max_wall_seconds == 0
    assert goal.max_tokens == 0
