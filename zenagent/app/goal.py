"""Goal state, argument parsing and the goal budget vocabulary.

A goal is session-scoped: one objective, an optional deterministic check, and a
budget. This module is deliberately UI-agnostic and does not import Chat,
Runtime or any frontend. ``ChatController`` owns scheduling; ``Runtime`` owns
the session binding.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Literal, Protocol, cast

from zenagent.agent.session import Session
from zenagent.app import records
from zenagent.app.views import GoalView
from zenagent.types import Message, Usage

if TYPE_CHECKING:
    from zenagent.limits import Limits

__all__ = [
    "GOAL_STATUSES",
    "GoalAction",
    "GoalArgs",
    "GoalController",
    "GoalDecision",
    "GoalLimits",
    "GoalState",
    "GoalStatus",
    "GoalTurnContext",
    "GoalVerdict",
    "GoalVerdictKind",
    "GoalVerifier",
    "GoalVerifierError",
    "goal_tokens",
    "parse_goal_args",
    "render_goal_prompt",
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

    @classmethod
    def from_limits(cls, limits: Limits) -> GoalLimits:
        return cls(
            max_turns=limits.goal_max_turns,
            max_tokens=limits.goal_max_tokens,
            max_wall_seconds=limits.goal_max_wall_minutes * 60,
            check_timeout_s=limits.goal_check_timeout_s,
            verifier_max_tokens=limits.goal_verifier_max_tokens,
        )


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


GoalVerdictKind = Literal["met", "not_met", "impossible"]


@dataclass(frozen=True)
class GoalVerdict:
    """One verification result."""

    verdict: GoalVerdictKind
    reason: str
    evidence: str = ""
    tokens: int = 0


@dataclass
class GoalTurnContext:
    """What one finished turn contributes to the goal decision."""

    messages: list[Message]
    auto: bool
    tool_calls: int
    termination: str | None
    tokens_delta: int
    subagent_tokens_delta: int
    elapsed_s: float
    user_queued: bool


class GoalVerifierError(RuntimeError):
    """The verifier could not produce a verdict (provider or parse failure)."""


@dataclass(frozen=True)
class GoalDecision:
    """What ChatController should do after one turn."""

    action: Literal["continue", "stop"]
    status: GoalStatus
    prompt: str | None = None
    reason: str = ""
    wrap_up: bool = False


class GoalVerifier(Protocol):
    """Completion verifier: check command or an independent model call."""

    async def verify(self, goal: GoalState, ctx: GoalTurnContext) -> GoalVerdict:
        ...


class GoalController:
    """Owns one session's goal state, persistence and lifecycle.

    The controller is deliberately UI-agnostic: it never imports Chat, Runtime
    or a frontend. ``ChatController`` asks it what to do next; ``Runtime`` binds
    it to the session in context.
    """

    def __init__(
        self,
        *,
        limits: GoalLimits | None = None,
        verifier: GoalVerifier | None = None,
        check_verifier: GoalVerifier | None = None,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._limits = limits or GoalLimits()
        self._verifier = verifier
        self._check_verifier = check_verifier
        self._now = now
        self._state: GoalState | None = None
        self._session: Session | None = None
        self._budget_abort = False
        self._turn_auto = True
        self._turn_started_at = 0.0

    @property
    def state(self) -> GoalState | None:
        return self._state

    def configure(
        self,
        *,
        limits: GoalLimits,
        verifier: GoalVerifier | None,
        check_verifier: GoalVerifier | None = None,
    ) -> None:
        """Update defaults for future goals; the current goal keeps its budget."""

        self._limits = limits
        self._verifier = verifier
        self._check_verifier = check_verifier

    def bind_session(self, session: Session | None) -> None:
        """Load the last goal record from ``session`` (or clear when ``None``)."""

        self._session = session
        if session is None:
            self._state = None
            return
        session.ensure_loaded()
        state: GoalState | None = None
        for record in session.records:
            if not records.is_goal(record):
                continue
            payload = record.get("goal")
            if payload is None:
                state = None
                continue
            if not isinstance(payload, dict):
                continue
            try:
                state = GoalState.from_dict(payload)
            except ValueError:
                continue
        self._state = state

    def set(
        self,
        objective: str,
        *,
        check: str | None = None,
        max_turns: int | None = None,
    ) -> GoalState:
        state = GoalState.new(
            objective,
            check=check,
            limits=self._limits,
            max_turns=max_turns,
        )
        stamp = self._now()
        state.created_at = stamp
        state.updated_at = stamp
        self._state = state
        self._persist()
        return state

    def pause(self) -> GoalState | None:
        state = self._state
        if state is None or state.status != "active":
            return None
        state.status = "paused"
        state.updated_at = self._now()
        self._persist()
        return state

    def resume(self) -> GoalState | None:
        state = self._state
        if state is None or state.status not in ("paused", "blocked"):
            return None
        state.status = "active"
        state.no_progress_turns = 0
        state.verifier_failures = 0
        state.check_failures = 0
        state.last_check_key = ""
        state.updated_at = self._now()
        self._persist()
        return state

    def clear(self) -> None:
        self._state = None
        self._persist()

    def snapshot(self) -> GoalView | None:
        state = self._state
        if state is None:
            return None
        return GoalView(
            objective=state.objective,
            status=state.status,
            check=state.check,
            turns_used=state.turns_used,
            max_turns=state.max_turns,
            tokens_used=state.tokens_used,
            max_tokens=state.max_tokens,
            wall_seconds=state.wall_seconds_used,
            max_wall_seconds=state.max_wall_seconds,
            last_verdict=state.last_verdict,
            last_reason=state.last_reason,
            updated_at=int(state.updated_at),
        )

    def note_turn_start(self, *, auto: bool) -> None:
        self._turn_auto = auto
        self._turn_started_at = self._now()
        self._budget_abort = False

    def note_budget_abort(self) -> None:
        self._budget_abort = True

    def budget_exceeded(
        self, *, tokens_delta: int = 0, elapsed_s: float = 0.0
    ) -> bool:
        state = self._state
        if state is None or state.status != "active":
            return False
        return (
            state.tokens_used + max(tokens_delta, 0) >= state.max_tokens
            or state.wall_seconds_used + max(elapsed_s, 0.0)
            >= state.max_wall_seconds
        )

    async def after_turn(self, ctx: GoalTurnContext) -> GoalDecision:
        state = self._state
        if state is None or state.status != "active":
            return GoalDecision(
                "stop", state.status if state is not None else "complete"
            )

        state.tokens_used += max(ctx.tokens_delta, 0) + max(
            ctx.subagent_tokens_delta, 0
        )
        state.wall_seconds_used += max(ctx.elapsed_s, 0.0)

        if ctx.termination != "stop":
            if ctx.termination == "aborted":
                if self._budget_abort:
                    self._budget_abort = False
                    return self._finish(
                        "budget_limited", "goal budget reached while running"
                    )
                return self._finish("paused", "run aborted")
            return self._finish("blocked", f"run ended: {ctx.termination}")

        state.turns_used += 1
        state.updated_at = self._now()

        if state.tokens_used >= state.max_tokens:
            return self._finish(
                "budget_limited",
                f"token budget reached ({state.tokens_used}/{state.max_tokens})",
            )
        if state.wall_seconds_used >= state.max_wall_seconds:
            return self._finish(
                "budget_limited",
                "wall-clock budget reached "
                f"({state.wall_seconds_used:.0f}/{state.max_wall_seconds}s)",
            )
        if state.turns_used > state.max_turns:
            return self._finish(
                "budget_limited",
                f"turn budget reached ({state.turns_used}/{state.max_turns})",
            )
        if state.turns_used >= state.max_turns:
            self._persist()
            return GoalDecision(
                "continue",
                "active",
                prompt=render_goal_prompt(state, wrap_up=True),
                reason="turn budget reached; wrapping up",
                wrap_up=True,
            )

        if ctx.auto and ctx.tool_calls == 0:
            state.no_progress_turns += 1
        else:
            state.no_progress_turns = 0
        if state.no_progress_turns >= 3:
            return self._finish("blocked", "no progress for 3 goal turns")

        verifier = self._check_verifier if state.check else self._verifier
        if verifier is None:
            return self._finish("blocked", "no verifier available")

        try:
            verdict = await verifier.verify(state, ctx)
        except GoalVerifierError as exc:
            state.verifier_failures += 1
            state.last_reason = str(exc)
            state.updated_at = self._now()
            if state.verifier_failures >= 3:
                return self._finish(
                    "blocked", f"verifier failed 3 times: {exc}"
                )
            self._persist()
            return GoalDecision(
                "continue",
                "active",
                prompt=render_goal_prompt(state),
                reason=str(exc),
            )

        state.verifier_failures = 0
        state.last_verdict = verdict.verdict
        state.last_reason = verdict.reason
        state.updated_at = self._now()

        if verdict.verdict == "met":
            return self._finish("complete", verdict.reason)
        if verdict.verdict == "impossible":
            return self._finish("blocked", verdict.reason)

        if state.check:
            key = _check_key(verdict)
            if key == state.last_check_key:
                state.check_failures += 1
            else:
                state.check_failures = 1
            state.last_check_key = key
            if state.check_failures >= 3:
                return self._finish(
                    "blocked",
                    f"check keeps failing identically: {verdict.reason}",
                )

        self._persist()
        return GoalDecision(
            "continue",
            "active",
            prompt=render_goal_prompt(state),
            reason=verdict.reason,
        )

    def _finish(self, status: GoalStatus, reason: str) -> GoalDecision:
        state = self._state
        if state is not None:
            state.status = status
            state.last_reason = reason
            state.updated_at = self._now()
            self._persist()
        return GoalDecision("stop", status, reason=reason)

    def _persist(self) -> None:
        if self._session is None:
            return
        payload = self._state.to_dict() if self._state is not None else None
        self._session.append_meta(records.goal(payload))


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


def render_goal_prompt(state: GoalState, *, wrap_up: bool = False) -> str:
    """The user-visible continuation directive; objective is untrusted data."""

    if wrap_up:
        instruction = (
            "The turn budget is reached. Do not start new work. Summarize useful "
            "progress, remaining work, blockers and the next step."
        )
    else:
        instruction = (
            "Take the next concrete step. Do not restate the plan. Do not ask the "
            "user a question unless the goal is blocked. The controller runs the "
            "configured check; use the last verification reason below to decide "
            "the next step. Do not start work outside the objective."
        )
    return (
        "[goal] Continue working toward the objective below.\n\n"
        f"<objective>\n{_escape(state.objective)}\n</objective>\n\n"
        f"Budget: turn {state.turns_used}/{state.max_turns}, "
        f"tokens {state.tokens_used}/{state.max_tokens}, "
        f"elapsed {state.wall_seconds_used:.0f}/{state.max_wall_seconds}s.\n"
        f"Last verification: {state.last_verdict or 'none'} — "
        f"{state.last_reason or 'no verdict yet'}\n\n"
        f"{instruction}"
    )


def _check_key(verdict: GoalVerdict) -> str:
    evidence = verdict.evidence.strip()
    tail = evidence.splitlines()[-1] if evidence else ""
    return f"{verdict.reason}|{tail}"


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


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
