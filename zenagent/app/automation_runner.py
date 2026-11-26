"""Run one automation round: lease the session, prompt, settle, release.

The whole point of this module is that a round is the *same* thing a person
typing into that session would produce, so it reuses ``ChatController`` instead
of driving the agent directly: permissions, event flow, persistence, aborts and
the run ledger all behave exactly as they do interactively.

Two properties are the reason for the unusual shape below:

* **Hot process, cold transcript.** The daemon keeps one Runtime alive (MCP,
  skills and the catalog stay connected), but every round re-reads the session
  from disk through ``switch_session`` so that anything the user said in that
  session since the last round is in context.
* **The lease is for the round, never for the idle.** A resident Runtime holds
  its session's write lease for its whole life, which would lock the user out
  of a session they are entitled to open. So the lease is taken at the start of
  a round and released in ``finally``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from pathlib import Path

from zenagent.agent.session import unique_session_path
from zenagent.app.automations import Job, resolve_session, wrap_prompt
from zenagent.app.run_wiring import RunRecorder, update_run_scope
from zenagent.app.session_lease import SessionLeaseBusy, release_session_lease
from zenagent.events import AgentEvent

__all__ = [
    "ROUND_STATUSES",
    "RoundResult",
    "run_round",
]

_log = logging.getLogger(__name__)

#: How a round can end. ``skipped`` is not a failure: the session was in use.
ROUND_STATUSES: tuple[str, ...] = ("done", "failed", "timeout", "skipped", "error")


@dataclass(frozen=True)
class RoundResult:
    """One round's outcome, in the vocabulary the scheduler records."""

    status: str
    skip_reason: str = ""
    error: str = ""
    #: The session file name this round actually used. It differs from
    #: ``job.session`` when that transcript was missing and got re-minted.
    session: str = ""
    #: The ledger row for this round (empty when the round never started).
    run_id: str = ""


class _RunIdSpy:
    """Remember this round's run id: the recorder clears it when the run ends."""

    def __init__(self, runtime) -> None:
        self._runtime = runtime
        self.run_id: str = ""

    def __call__(self, event: AgentEvent) -> None:
        if event.type == "agent_start" and not self.run_id:
            self.run_id = str(getattr(self._runtime, "current_run_id", "") or "")


def _target_session(root: Path, name: str) -> tuple[Path, str]:
    """The transcript to use, minting a replacement when it vanished.

    A deleted transcript must not wedge the schedule: the job keeps running into
    a fresh file and the caller records the new name in state.
    """
    path = resolve_session(root, name)
    if path.exists():
        return path, name
    minted = unique_session_path(root=root)
    _log.warning(
        "automation session %s is missing; writing this round to %s", path, minted.name
    )
    return minted, minted.name


async def run_round(
    runtime,
    chat,
    job: Job,
    *,
    run_number: int,
    now: float,
    session_root: Path,
) -> RoundResult:
    """Run ``job`` once and report how it ended."""
    target, session_name = _target_session(session_root, job.session)

    update_run_scope(runtime, kind="automation", label=job.name)
    try:
        await runtime.switch_session(target)
    except SessionLeaseBusy:
        # The user has this session open. Do not steal it and do not queue a
        # backlog: the next cycle tries again.
        return RoundResult(
            status="skipped", skip_reason="session-busy", session=session_name
        )

    spy = _RunIdSpy(runtime)
    unsubscribe = runtime.agent.subscribe(spy)
    watchdog: asyncio.Task | None = None
    status = "done"
    error = ""
    try:
        await chat.submit(wrap_prompt(job, run_number, now))
        idle = asyncio.create_task(chat.wait_for_idle())
        watchdog = asyncio.create_task(_sleep_then_abort(chat, job.timeout_s))
        done, _pending = await asyncio.wait(
            {idle, watchdog}, return_when=asyncio.FIRST_COMPLETED
        )
        if watchdog in done and idle not in done:
            status = "timeout"
            # Wait for the abort to actually land before the lease is released,
            # otherwise the next process can read a half-written transcript.
            await chat.wait_for_idle()
        else:
            # Surface an exception from the run task instead of swallowing it.
            idle.result()
        if chat.busy:
            status = "error"
            error = "the run did not settle after abort"
    finally:
        if watchdog is not None:
            # Must be cancelled: a leftover watchdog would abort the *next* job.
            watchdog.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watchdog
        unsubscribe()
        release_session_lease(runtime)

    if status == "timeout":
        RunRecorder(runtime).relabel(
            status="failed", termination="timeout", run_id=spy.run_id
        )
    elif status == "error":
        RunRecorder(runtime).relabel(
            status="failed", termination="error", error=error, run_id=spy.run_id
        )

    return RoundResult(
        status=status, error=error, session=session_name, run_id=spy.run_id
    )


async def _sleep_then_abort(chat, seconds: float) -> None:
    """Abort the run cooperatively once the wall-clock budget runs out.

    Deliberately *not* ``asyncio.wait_for(chat.wait_for_idle())``: cancelling
    that await cancels the run task itself, whose ``finally`` may never clear
    the controller's busy flag — a wedged daemon with no trace. ``abort()`` is
    the protocol-level stop the kernel already understands.
    """
    await asyncio.sleep(seconds)
    chat.abort()
