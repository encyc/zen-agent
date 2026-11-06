"""Loop-guard heuristics: detecting a model stuck repeating itself.

Kept out of :mod:`zenagent.agent.loop` so the loop reads as a turn machine. The
corrective message it injects is model-facing only: it is never persisted, never
rendered as a user turn, and never counted by fork/retry — hence the prefix
check the UI uses to filter it.
"""

from __future__ import annotations

import json
import re

from zenagent.types import ToolCall, UserMessage

__all__ = [
    "LOOP_GUARD_PREFIX",
    "LOOP_GUARD_TEXT",
    "LOOP_GUARD_THRESHOLD",
    "calls_signature",
    "is_loop_guard_message",
    "is_poll_call",
]

LOOP_GUARD_THRESHOLD = 5

#: Prefix of the synthetic, non-persisted corrective message (the UI filters it).
LOOP_GUARD_PREFIX = "[tool-loop guard]"

LOOP_GUARD_TEXT = (
    LOOP_GUARD_PREFIX + " The same tool call has repeated {n} times with identical "
    "arguments and produced no new progress. Stop repeating it: change the "
    "arguments, use a different tool, or explain to the user what is blocking you."
)

#: Commands that are legitimate to repeat: they observe a changing state.
POLL_COMMAND = re.compile(r"^\s*(sleep|wait|tail|ps|pgrep|top|jobs)\b")


def is_loop_guard_message(message: object) -> bool:
    """Whether ``message`` is the synthetic loop-guard corrective."""
    return isinstance(message, UserMessage) and str(
        getattr(message, "content", "")
    ).startswith(LOOP_GUARD_PREFIX)


def is_poll_call(call: ToolCall) -> bool:
    """Whether this call is a pure wait/observe poll (exempt from the guard)."""
    if call.name == "wait":
        # Waiting for a background job is exactly the legitimate long poll.
        return True
    if call.name != "bash":
        return False
    return bool(POLL_COMMAND.match(str(call.arguments.get("command", ""))))


def calls_signature(calls: list[ToolCall]) -> tuple:
    """Stable signature of a tool batch, for detecting identical repeats."""
    return tuple(
        sorted(
            (call.name, json.dumps(call.arguments, sort_keys=True, default=str))
            for call in calls
        )
    )


