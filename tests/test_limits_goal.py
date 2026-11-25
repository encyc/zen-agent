from pathlib import Path

from zenagent.app.goal import GoalLimits
from zenagent.limits import Limits, parse_limits


def test_goal_defaults():
    """A goal carries no turn, token or wall-clock ceiling any more."""
    limits = Limits()
    assert limits.goal_check_timeout_s == 300
    assert limits.goal_verifier_max_tokens == 512
    for retired in ("goal_max_turns", "goal_max_wall_minutes", "goal_max_tokens"):
        assert not hasattr(limits, retired)


def test_parse_goal_table_keeps_the_surviving_knobs():
    limits = parse_limits(
        {
            "goal": {
                "check_timeout_s": 60,
                "verifier_max_tokens": 128,
            }
        },
        Path("config.toml"),
    )
    assert limits.goal_check_timeout_s == 60
    assert limits.goal_verifier_max_tokens == 128


def test_goal_limits_adapter_keeps_the_watchdog_knobs():
    goal = GoalLimits.from_limits(Limits(goal_check_timeout_s=60))
    assert goal.check_timeout_s == 60


def test_retired_goal_budgets_are_accepted_and_ignored(caplog):
    """An old config keeps starting; the keys simply do nothing now."""
    from zenagent import limits as limits_module

    limits_module._warned_retired.clear()  # the warning is once per process
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

    assert not hasattr(limits, "goal_max_turns")
    assert "retired" in caplog.text
