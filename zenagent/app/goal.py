"""Goal state, argument parsing and the goal budget vocabulary.

A goal is session-scoped: one objective, an optional deterministic check, and a
budget. This module is deliberately UI-agnostic and does not import Chat,
Runtime or any frontend. ``ChatController`` owns scheduling; ``Runtime`` owns
the session binding.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass
from typing import Literal, cast

from zenagent.types import Usage

__all__ = [
    "GOAL_STATUSES",
    "GoalAction",
    "GoalArgs",
    "GoalLimits",
    "GoalState",
    "GoalStatus",
    "goal_tokens",
    "parse_goal_args",
]

GoalStatus = Literal["active", "paused", "blocked", "budget_limited", "complete"]
GoalAction = Literal["set", "status", "pause", "resume", "clear"]

GOAL_STATUSES: frozenset[str] = frozenset(
    {"active", "paused", "blocked", "budget_limited", "complete"}
)

_OBJECTIVE_LIMIT = 4000
_LIFECYCLE: dict[str, GoalAction] = {
    "pause": "pause",
    "resume": "resume",
    "clear": "clear",
    "stop": "clear",
    "off": "clear",
    "cancel": "clear",
}


@dataclass(frozen=True)
class GoalLimits:
    """Defaults applied when a new goal is created."""

    max_turns: int = 40
    max_tokens: int = 2_000_000
    max_wall_seconds: int = 14_400
    check_timeout_s: int = 300
    verifier_max_tokens: int = 512


@dataclass
class GoalArgs:
    """One parsed ``/goal`` invocation."""

    action: GoalAction
    objective: str | None = None
    check: str | None = None
    max_turns: int | None = None


@dataclass
class GoalState:
    """The persisted state of one session's goal."""

    goal_id: str
    objective: str
    status: GoalStatus
    check: str | None
    max_turns: int
    max_tokens: int
    max_wall_seconds: int
    turns_used: int = 0
    tokens_used: int = 0
    wall_seconds_used: float = 0.0
    no_progress_turns: int = 0
    verifier_failures: int = 0
    check_failures: int = 0
    last_check_key: str = ""
    last_verdict: str | None = None
    last_reason: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0

    @classmethod
    def new(
        cls,
        objective: str,
        *,
        check: str | None = None,
        limits: GoalLimits | None = None,
        max_turns: int | None = None,
    ) -> GoalState:
        cleaned = objective.strip()
        if not cleaned:
            raise ValueError("goal objective is empty")
        if len(cleaned) > _OBJECTIVE_LIMIT:
            raise ValueError(f"goal objective is longer than {_OBJECTIVE_LIMIT} characters")
        if check is not None:
            check = check.strip()
            if not check:
                raise ValueError("goal check is empty")
        if max_turns is not None and max_turns <= 0:
            raise ValueError("goal max_turns must be positive")
        limits = limits or GoalLimits()
        now = time.time()
        return cls(
            goal_id=uuid.uuid4().hex,
            objective=cleaned,
            status="active",
            check=check,
            max_turns=max_turns if max_turns is not None else limits.max_turns,
            max_tokens=limits.max_tokens,
            max_wall_seconds=limits.max_wall_seconds,
            created_at=now,
            updated_at=now,
        )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> GoalState:
        status = data.get("status")
        if not isinstance(status, str) or status not in GOAL_STATUSES:
            raise ValueError(f"unknown goal status: {status!r}")
        defaults = GoalLimits()
        return cls(
            goal_id=_as_str(data.get("goal_id"), ""),
            objective=_as_str(data.get("objective"), ""),
            status=cast(GoalStatus, status),
            check=_as_optional_str(data.get("check")),
            max_turns=_as_int(data.get("max_turns"), defaults.max_turns),
            max_tokens=_as_int(data.get("max_tokens"), defaults.max_tokens),
            max_wall_seconds=_as_int(
                data.get("max_wall_seconds"), defaults.max_wall_seconds
            ),
            turns_used=_as_int(data.get("turns_used"), 0),
            tokens_used=_as_int(data.get("tokens_used"), 0),
            wall_seconds_used=_as_float(data.get("wall_seconds_used"), 0.0),
            no_progress_turns=_as_int(data.get("no_progress_turns"), 0),
            verifier_failures=_as_int(data.get("verifier_failures"), 0),
            check_failures=_as_int(data.get("check_failures"), 0),
            last_check_key=_as_str(data.get("last_check_key"), ""),
            last_verdict=_as_optional_str(data.get("last_verdict")),
            last_reason=_as_str(data.get("last_reason"), ""),
            created_at=_as_float(data.get("created_at"), 0.0),
            updated_at=_as_float(data.get("updated_at"), 0.0),
        )


def parse_goal_args(argument: str) -> GoalArgs:
    """Parse the text after ``/goal``.

    Flags are only recognized before the objective, so an objective may contain
    ``--check`` literally. ``--check`` accepts a quoted value; ``--budget`` is a
    positive turn count.
    """

    rest = argument.strip()
    if not rest:
        return GoalArgs(action="status")
    if rest in _LIFECYCLE:
        return GoalArgs(action=_LIFECYCLE[rest])

    check: str | None = None
    max_turns: int | None = None
    while True:
        if rest == "--check" or rest.startswith("--check "):
            value, rest = _take_flag_value(rest[len("--check") :].lstrip())
            if not value.strip():
                raise ValueError("--check needs a command")
            check = value
            continue
        if rest == "--budget" or rest.startswith("--budget "):
            value, rest = _take_flag_value(rest[len("--budget") :].lstrip())
            try:
                parsed = int(value)
            except ValueError as exc:
                raise ValueError("--budget needs a positive integer") from exc
            if parsed <= 0:
                raise ValueError("--budget needs a positive integer")
            max_turns = parsed
            continue
        break

    objective = rest.strip()
    if not objective:
        raise ValueError("goal objective is empty")
    if len(objective) > _OBJECTIVE_LIMIT:
        raise ValueError(f"goal objective is longer than {_OBJECTIVE_LIMIT} characters")
    return GoalArgs(
        action="set", objective=objective, check=check, max_turns=max_turns
    )


def goal_tokens(usage: Usage, api: str) -> int:
    """Provider-normalized prompt+output tokens for budget accounting.

    ``Usage.input`` includes cached tokens on OpenAI-style APIs but excludes
    cache reads/writes on Anthropic; mirror ``context_occupancy`` so the budget
    means the same thing on every provider.
    """

    prompt = usage.input
    if api == "anthropic-messages":
        prompt += usage.cache_read + usage.cache_write
    return prompt + usage.output


def _take_flag_value(text: str) -> tuple[str, str]:
    """Take one quoted or whitespace-delimited value; return ``(value, rest)``."""

    text = text.lstrip()
    if not text:
        raise ValueError("missing flag value")
    quote = text[0]
    if quote in {"'", '"'}:
        end = text.find(quote, 1)
        if end < 0:
            raise ValueError("unterminated quoted flag value")
        return text[1:end], text[end + 1 :].lstrip()
    parts = text.split(None, 1)
    return parts[0], parts[1] if len(parts) > 1 else ""


def _as_str(value: object, default: str) -> str:
    return value if isinstance(value, str) else default


def _as_optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _as_int(value: object, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return default


def _as_float(value: object, default: float) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    return default
