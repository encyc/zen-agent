"""The `/goal` command body.

``commands.py`` keeps the registry wrapper; parsing, status formatting and
lifecycle dispatch live here so the command table stays a table.
"""

from __future__ import annotations

from collections.abc import Callable

from zenagent.app.goal import GoalController, parse_goal_args
from zenagent.app.views import GoalView, goal_limit_text
from zenagent.i18n import t

__all__ = ["run_goal_command"]


def run_goal_command(
    argument: str,
    controller: GoalController,
    *,
    start_goal: Callable[..., str],
    resume_goal: Callable[[], None],
) -> str:
    try:
        args = parse_goal_args(argument)
    except ValueError as exc:
        return t("cmdmsg.goal_usage", error=exc)

    if args.action == "status":
        return _goal_status(controller.snapshot())
    if args.action == "pause":
        state = controller.pause()
        if state is None:
            return t("cmdmsg.goal_no_active")
        return t("cmdmsg.goal_paused")
    if args.action == "resume":
        state = controller.resume()
        if state is None:
            return t("cmdmsg.goal_no_resume")
        resume_goal()
        return t("cmdmsg.goal_resumed")
    if args.action == "clear":
        controller.clear()
        return t("cmdmsg.goal_cleared")

    assert args.objective is not None
    return start_goal(
        args.objective, check=args.check, max_turns=args.max_turns
    )


def _goal_status(view: GoalView | None) -> str:
    if view is None:
        return t("cmdmsg.goal_none")
    return t(
        "cmdmsg.goal_status",
        objective=view.objective,
        status=view.status,
        check=view.check or "-",
        turns=view.turns_used,
        max_turns=goal_limit_text(view.max_turns),
        tokens=view.tokens_used,
        max_tokens=goal_limit_text(view.max_tokens),
        wall=int(view.wall_seconds),
        max_wall=goal_limit_text(view.max_wall_seconds),
        last=f"{view.last_verdict or 'none'} — {view.last_reason or 'no verdict yet'}",
    )
