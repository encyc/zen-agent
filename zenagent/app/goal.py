"""Goal state, argument parsing and the goal budget vocabulary.

A goal is session-scoped: one objective, an optional deterministic check, and a
budget. This module is deliberately UI-agnostic and does not import Chat,
Runtime or any frontend. ``ChatController`` owns scheduling; ``Runtime`` owns
the session binding.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Literal, Protocol, cast

from zenagent import settings
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


#: A zero ceiling means "no limit" on every budget knob.
_NO_LIMIT = 0


@dataclass(frozen=True)
class GoalLimits:
    """Defaults applied when a new goal is created.

    A goal has no turn, token or wall-clock ceiling: it stops on a verdict, a
    blocker, the user, or a run the harness had to interrupt.
    """

    check_timeout_s: int = 300
    verifier_max_tokens: int = 512

    @classmethod
    def from_limits(cls, limits: Limits) -> GoalLimits:
        return cls(
            check_timeout_s=limits.goal_check_timeout_s,
            verifier_max_tokens=limits.goal_verifier_max_tokens,
        )


@dataclass
class GoalArgs:
    """One parsed ``/goal`` invocation."""

    action: GoalAction
    objective: str | None = None
    check: str | None = None


@dataclass
class GoalState:
    """The persisted state of one session's goal."""

    goal_id: str
    objective: str
    status: GoalStatus
    check: str | None
    #: Usage counters. They are reported, never enforced.
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
        limits = limits or GoalLimits()
        now = time.time()
        return cls(
            goal_id=uuid.uuid4().hex,
            objective=cleaned,
            status="active",
            check=check,
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
        return cls(
            goal_id=_as_str(data.get("goal_id"), ""),
            objective=_as_str(data.get("objective"), ""),
            status=cast(GoalStatus, status),
            check=_as_optional_str(data.get("check")),
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
    #: The reason behind ``termination`` when the kernel gave one.
    detail: str | None = None


class GoalVerifierError(RuntimeError):
    """The verifier could not produce a verdict (provider or parse failure)."""


@dataclass(frozen=True)
class GoalDecision:
    """What ChatController should do after one turn."""

    action: Literal["continue", "stop"]
    status: GoalStatus
    prompt: str | None = None
    reason: str = ""


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
        self._turn_auto = True
        self._turn_started_at = 0.0
        #: Check goals created/resumed in this process are trusted; a goal
        #: loaded from disk by a fresh controller needs /goal resume first.
        self._trusted_checks: set[str] = set()
        #: Bumped by every lifecycle change; a verifier result from an older
        #: generation is discarded instead of being applied to the new goal.
        self._generation = 0
        self._verify_task: asyncio.Task[GoalVerdict] | None = None

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
        """Update the defaults applied when a new goal is created."""

        self._limits = limits
        self._verifier = verifier
        self._check_verifier = check_verifier

    def _invalidate(self) -> None:
        self._generation += 1
        task = self._verify_task
        if task is not None and not task.done():
            task.cancel()

    def _still_current(
        self,
        generation: int,
        state: GoalState,
        session: Session | None,
    ) -> bool:
        return (
            self._generation == generation
            and self._state is state
            and self._session is session
            and state.status == "active"
        )

    def bind_session(self, session: Session | None) -> None:
        """Load the last goal record from ``session`` (or clear when ``None``)."""

        self._invalidate()
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
        if (
            state is not None
            and state.status == "active"
            and state.check
            and state.goal_id not in self._trusted_checks
        ):
            state.status = "paused"
            state.last_reason = "check needs confirmation; run /goal resume"
            state.updated_at = self._now()
            self._persist()

    def on_session_leave(self, session: Session | None) -> None:
        """Pause the old session's goal when the user asked to leave-pause."""

        if session is None or session is not self._session:
            return
        if not settings.get_pref("goal_pause_on_session_switch", False):
            return
        state = self._state
        if state is None or state.status != "active":
            return
        state.status = "paused"
        state.last_reason = "session switched"
        state.updated_at = self._now()
        self._persist()

    def set(
        self,
        objective: str,
        *,
        check: str | None = None,
    ) -> GoalState:
        self._invalidate()
        state = GoalState.new(
            objective,
            check=check,
            limits=self._limits,
        )
        stamp = self._now()
        state.created_at = stamp
        state.updated_at = stamp
        self._state = state
        if state.check:
            self._trusted_checks.add(state.goal_id)
        self._persist()
        return state

    def pause(self) -> GoalState | None:
        state = self._state
        if state is None or state.status != "active":
            return None
        self._invalidate()
        state.status = "paused"
        state.updated_at = self._now()
        self._persist()
        return state

    def resume(self) -> GoalState | None:
        state = self._state
        if state is None or state.status not in ("paused", "blocked"):
            return None
        self._invalidate()
        state.status = "active"
        state.no_progress_turns = 0
        state.verifier_failures = 0
        state.check_failures = 0
        state.last_check_key = ""
        if state.check:
            self._trusted_checks.add(state.goal_id)
        state.updated_at = self._now()
        self._persist()
        return state

    def clear(self) -> None:
        self._invalidate()
        if self._state is not None:
            self._trusted_checks.discard(self._state.goal_id)
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
            tokens_used=state.tokens_used,
            wall_seconds=state.wall_seconds_used,
            last_verdict=state.last_verdict,
            last_reason=state.last_reason,
            updated_at=int(state.updated_at),
        )

    def note_turn_start(self, *, auto: bool) -> None:
        self._turn_auto = auto
        self._turn_started_at = self._now()

    def settle_turn(self, ctx: GoalTurnContext) -> bool:
        """Charge one finished turn to the goal budget, even if it will stop."""

        state = self._state
        if state is None or state.status != "active":
            return False
        state.tokens_used += max(ctx.tokens_delta, 0) + max(
            ctx.subagent_tokens_delta, 0
        )
        state.wall_seconds_used += max(ctx.elapsed_s, 0.0)
        if ctx.termination == "stop":
            state.turns_used += 1
        state.updated_at = self._now()
        self._persist()
        return True

    async def after_turn(
        self, ctx: GoalTurnContext, *, account: bool = True
    ) -> GoalDecision:
        state = self._state
        if state is None or state.status != "active":
            return GoalDecision(
                "stop", state.status if state is not None else "complete"
            )
        generation = self._generation
        session = self._session
        if account:
            self.settle_turn(ctx)

        if ctx.termination != "stop":
            if ctx.termination == "aborted":
                return self._finish("paused", "run aborted")
            if ctx.termination == "loop_detected":
                # Recoverable by design: the user can resume once they see why.
                return self._finish(
                    "paused", f"run interrupted: {ctx.detail or 'a repeated batch'}"
                )
            return self._finish("blocked", f"run ended: {ctx.termination}")


        if ctx.auto and ctx.tool_calls == 0:
            state.no_progress_turns += 1
        else:
            state.no_progress_turns = 0
        if state.no_progress_turns >= 3:
            return self._finish("blocked", "no progress for 3 goal turns")

        verifier = self._check_verifier if state.check else self._verifier
        if verifier is None:
            return self._finish("blocked", "no verifier available")

        verify_started = self._now()
        task = asyncio.create_task(verifier.verify(state, ctx))
        self._verify_task = task
        try:
            verdict = await task
        except asyncio.CancelledError:
            return GoalDecision(
                "stop",
                self._state.status if self._state is not None else "paused",
                reason="verification cancelled",
            )
        except GoalVerifierError as exc:
            if not self._still_current(generation, state, session):
                return GoalDecision(
                    "stop",
                    self._state.status
                    if self._state is not None
                    else "complete",
                    reason="goal changed during verification",
                )
            state.verifier_failures += 1
            state.last_reason = str(exc)
            state.wall_seconds_used += max(0.0, self._now() - verify_started)
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
        finally:
            self._verify_task = None

        if not self._still_current(generation, state, session):
            return GoalDecision(
                "stop",
                self._state.status if self._state is not None else "complete",
                reason="goal changed during verification",
            )

        state.wall_seconds_used += max(0.0, self._now() - verify_started)
        state.tokens_used += max(0, verdict.tokens)
        state.updated_at = self._now()

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
        if payload is not None:
            # A goal is a real session fact even before the first message:
            # materialize the lazy file so a switch cannot lose the objective.
            self._session.materialize()
        self._session.append_meta(records.goal(payload))


def parse_goal_args(argument: str) -> GoalArgs:
    """Parse the text after ``/goal``.

    Flags are only recognized before the objective, so an objective may contain
    ``--check`` literally. ``--check`` accepts a quoted value; ``--budget`` is a
    turn count, and 0 turns the turn ceiling off.
    """

    rest = argument.strip()
    if not rest:
        return GoalArgs(action="status")
    if rest in _LIFECYCLE:
        return GoalArgs(action=_LIFECYCLE[rest])

    check: str | None = None
    while True:
        if rest == "--check" or rest.startswith("--check "):
            value, rest = _take_flag_value(rest[len("--check") :].lstrip())
            if not value.strip():
                raise ValueError("--check needs a command")
            check = value
            continue
        if rest == "--budget" or rest.startswith("--budget "):
            raise ValueError(
                "--budget is gone: a goal has no turn ceiling and stops on a verdict"
            )
        break

    objective = rest.strip()
    if not objective:
        raise ValueError("goal objective is empty")
    if len(objective) > _OBJECTIVE_LIMIT:
        raise ValueError(f"goal objective is longer than {_OBJECTIVE_LIMIT} characters")
    return GoalArgs(
        action="set", objective=objective, check=check
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


def render_goal_prompt(state: GoalState) -> str:
    """The user-visible continuation directive; objective is untrusted data."""

    instruction = (
        "Take the next concrete step. Do not restate the plan. Do not ask the "
        "user a question unless the goal is blocked. The controller runs the "
        "configured check; use the last verification reason below to decide "
        "the next step. Do not start work outside the objective."
    )
    return (
        "[goal] Continue working toward the objective below.\n\n"
        f"<objective>\n{_escape(state.objective)}\n</objective>\n\n"
        f"Usage so far: {state.turns_used} turns, {state.tokens_used} tokens, "
        f"{state.wall_seconds_used:.0f}s. Nothing bounds this but the objective.\n"
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
