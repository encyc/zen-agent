"""Goal-loop glue for ChatController: continuation, budget watchdog, commands.

Keeping this here holds ``chat.py`` below the architecture line budget and
keeps the goal policy in one place. The functions take the controller as their
first argument; they are not a second state owner.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from zenagent.app.error_notice import bug_notice
from zenagent.app.goal import (
    GoalTurnContext,
    goal_tokens,
    render_goal_prompt,
)
from zenagent.app.views import usage_total
from zenagent.i18n import t

if TYPE_CHECKING:
    from zenagent.app.chat import ChatController
    from zenagent.app.goal import GoalController


def start_goal(
    chat: ChatController,
    objective: str,
    *,
    check: str | None = None,
    max_turns: int | None = None,
) -> str:
    goal = _goal_controller(chat)
    if goal is None:
        raise RuntimeError("goal controller is not wired")
    state = goal.set(objective, check=check, max_turns=max_turns)
    if chat.busy:
        chat._notify()
        return t("cmdmsg.goal_start_deferred")
    chat._running = True
    chat._task = asyncio.create_task(
        chat._run(render_goal_prompt(state), goal=True)
    )
    chat._notify()
    return t("cmdmsg.goal_started")


def maybe_continue_goal(chat: ChatController) -> None:
    goal = _goal_controller(chat)
    if goal is None or chat.busy or chat._goal_checking:
        return
    if chat._pending_queue.items:
        return
    state = goal.state
    if state is None or state.status != "active":
        return
    chat._running = True
    chat._task = asyncio.create_task(
        chat._run(render_goal_prompt(state), goal=True)
    )
    chat._notify()


def on_frontend_ready(chat: ChatController) -> None:
    if chat._frontend_ready:
        return
    chat._frontend_ready = True
    maybe_continue_goal(chat)


def begin_goal_turn(chat: ChatController, goal: bool) -> None:
    chat._goal_turn = goal
    chat._turn_tool_calls = 0
    chat._goal_usage_at_start = usage_total(chat.runtime.agent.messages)
    chat._goal_subagent_tokens_at_start = chat._subagents.tokens
    chat._goal_started_at = time.monotonic()
    controller = _goal_controller(chat)
    if controller is None:
        return
    controller.note_turn_start(auto=goal)
    if controller.state is not None:
        chat._goal_watchdog = asyncio.create_task(_watch(chat))


async def end_goal_turn(chat: ChatController, *, termination: str | None) -> bool:
    """Evaluate after a turn; True when a goal continuation was started."""

    goal = _goal_controller(chat)
    if goal is None or goal.state is None or goal.state.status != "active":
        return False
    ctx = GoalTurnContext(
        messages=list(chat.runtime.agent.messages),
        auto=chat._goal_turn,
        tool_calls=chat._turn_tool_calls,
        termination=termination,
        tokens_delta=_goal_tokens_delta(chat),
        subagent_tokens_delta=_goal_subagent_tokens_delta(chat),
        elapsed_s=(
            time.monotonic() - chat._goal_started_at
            if chat._goal_started_at is not None
            else 0.0
        ),
        user_queued=False,
    )
    chat._goal_checking = True
    chat._notify()
    try:
        decision = await goal.after_turn(ctx)
    except Exception as exc:  # noqa: BLE001 - never kill the session
        chat._last_error = bug_notice(repr(exc))
        decision = None
    finally:
        chat._goal_checking = False
    if (
        decision is not None
        and decision.action == "continue"
        and decision.prompt
    ):
        chat._running = False
        chat._task = None
        chat._notify()
        chat._task = asyncio.create_task(
            chat._run(decision.prompt, goal=True)
        )
        return True
    return False


def check_goal_budget(chat: ChatController) -> None:
    goal = _goal_controller(chat)
    if goal is None or chat._goal_started_at is None:
        return
    if goal.state is None or goal.state.status != "active":
        return
    tokens = _goal_tokens_delta(chat) + _goal_subagent_tokens_delta(chat)
    elapsed = time.monotonic() - chat._goal_started_at
    if goal.budget_exceeded(tokens_delta=tokens, elapsed_s=elapsed):
        goal.note_budget_abort()
        chat.runtime.agent.abort()


async def _watch(chat: ChatController) -> None:
    try:
        while True:
            await asyncio.sleep(5)
            check_goal_budget(chat)
    except asyncio.CancelledError:
        return


def _goal_tokens_delta(chat: ChatController) -> int:
    api = chat.runtime.agent.model.api
    current = usage_total(chat.runtime.agent.messages)
    return max(
        0,
        goal_tokens(current, api)
        - goal_tokens(chat._goal_usage_at_start, api),
    )


def _goal_subagent_tokens_delta(chat: ChatController) -> int:
    return max(0, chat._subagents.tokens - chat._goal_subagent_tokens_at_start)


def _goal_controller(chat: ChatController) -> GoalController | None:
    return getattr(chat.runtime, "goal", None)
