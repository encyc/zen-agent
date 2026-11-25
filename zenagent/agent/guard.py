"""Runaway detection: interrupt a model that repeats the same call batch.

A model can get stuck re-issuing byte-identical tool calls. The loop cannot tell
"stuck" from "busy" by watching a clock, but it can by watching the calls: a
batch whose signature equals the previous batch's asks the world for the same
thing again, so a run of identical batches means the run cannot make progress.

The detector is deliberately narrow. Poll-shaped calls (``wait``, ``sleep``,
``tail``, ``ps``, ...) observe a changing state, so a batch made only of them
resets the streak instead of extending it.

The detector reports; the loop decides. It returns the reason as a string, so
the run can end with ``loop_detected`` and hand the reason to the caller.
Nothing is written into the transcript and no corrective message ever exists —
which is why this module knows nothing about ``UserMessage``.
"""

from __future__ import annotations

import json
import re

from zenagent.types import ToolCall

__all__ = [
    "LOOP_GUARD_THRESHOLD",
    "POLL_COMMAND",
    "RunawayDetector",
    "calls_signature",
    "is_poll_call",
]

#: Identical batches in a row before the loop intervenes.
LOOP_GUARD_THRESHOLD = 5

#: Commands that are legitimate to repeat: they observe a changing state.
POLL_COMMAND = re.compile(r"^\s*(sleep|wait|tail|ps|pgrep|top|jobs)\b")

#: Longest argument rendering quoted in a run's stopping reason.
_REASON_ARGS_LIMIT = 120


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


class RunawayDetector:
    """Per-run repeat counter: one batch signature, one streak.

    Per run, not per session: a fresh instruction may legitimately ask for the
    same call the previous run made.
    """

    def __init__(self, threshold: int = LOOP_GUARD_THRESHOLD) -> None:
        self.threshold = max(int(threshold), 1)
        self._last: tuple | None = None
        self._streak = 0

    def observe(self, calls: list[ToolCall]) -> str | None:
        """Record one tool batch; return the reason when it is a runaway.

        ``None`` means "keep going". A non-empty string is the run's stopping
        detail: how many identical batches, and what the first call was.
        """
        if all(is_poll_call(call) for call in calls):
            self._streak = 0
            return None
        signature = calls_signature(calls)
        if signature == self._last:
            self._streak += 1
        else:
            self._last = signature
            self._streak = 1
        if self._streak < self.threshold:
            return None
        return f"{self._streak}x {_describe(calls)}"


def _describe(calls: list[ToolCall]) -> str:
    """Name the first call of a batch, arguments truncated to one line."""
    if not calls:
        return "(empty batch)"
    first = calls[0]
    args = json.dumps(first.arguments, sort_keys=True, default=str)
    if len(args) > _REASON_ARGS_LIMIT:
        args = args[: _REASON_ARGS_LIMIT - 3] + "..."
    return f"{first.name}({args})"
