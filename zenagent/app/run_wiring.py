"""Wire a Runtime to the project's run ledger.

The recorder is attached by :func:`~zenagent.app.bootstrap.prepare` for every
frontend. Nothing here changes agent behaviour: an
``agent_start`` opens a ledger record and an ``agent_end`` closes it, both
best-effort.

The scope is a plain dict on the Runtime, set by the owning layer before it
prompts the agent::

    {"kind": "coding", "agentId": "main", "label": "..."}
    {"kind": "subagent", "agentId": "task-1", "taskId": "t_..."}

A missing/unknown kind is recorded as ``coding`` so an embedder that never sets
a scope still gets a useful row.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from zenagent import project
from zenagent.app.runs import RunLedger, RunRecord, status_for_termination
from zenagent.app.session_lease import session_is_live
from zenagent.events import AgentEvent
from zenagent.types import AssistantMessage

__all__ = [
    "RunRecorder",
    "attach_run_recorder",
    "update_run_scope",
]

_log = logging.getLogger(__name__)

#: Ledgers already swept in this process. A project's crash debris is closed
#: once; later attaches only reconcile their own session, which is the cheap,
#: lease-backed case.
_SWEPT: set[str] = set()
_SWEEP_LOCK = threading.Lock()


def attach_run_recorder(runtime) -> RunLedger | None:
    """Attach one recorder to ``runtime.agent``; idempotent per Runtime.

    A pre-built runtime passed in by an embedder/test may not carry the app
    assembly (root/agent); that is not an error — there is simply nothing to
    record, and the caller keeps its own bookkeeping.
    """
    existing = getattr(runtime, "run_ledger", None)
    if existing is not None:
        return existing
    if getattr(runtime, "root", None) is None or getattr(runtime, "agent", None) is None:
        return None
    ledger = RunLedger(project.project_dir(root=runtime.root) / "runs.jsonl")
    runtime.run_ledger = ledger
    runtime.run_scope = {"kind": "coding", "agentId": "main"}
    runtime.current_run_id = None
    _reconcile(ledger, runtime)
    runtime.agent.subscribe(RunRecorder(runtime))
    return ledger


def _reconcile(ledger: RunLedger, runtime) -> None:
    """Close crash debris before this runtime opens its first run.

    This runtime holds its session's writer lease, so any older ``running``
    row for that session is dead. The rest of the project is swept once per
    process, using the lease as the liveness probe.
    """
    try:
        session = getattr(runtime, "session", None)
        ledger.reconcile_session(str(getattr(session, "path", "") or ""))
        key = str(ledger.path)
        with _SWEEP_LOCK:
            if key in _SWEPT:
                return
            _SWEPT.add(key)
        ledger.reconcile_stale(session_is_live)
    except Exception:  # noqa: BLE001 - recovery must never block startup
        # A failed sweep must not hold its "done" claim: the next attach
        # retries, instead of zombie running rows living forever (audit P2).
        with _SWEEP_LOCK:
            _SWEPT.discard(str(ledger.path))
        _log.debug("run reconciliation failed", exc_info=True)


def update_run_scope(runtime, **changes) -> dict:
    """Merge scope fields for the next run; ``None`` clears a field."""
    scope = dict(getattr(runtime, "run_scope", {}) or {})
    scope.update(changes)
    runtime.run_scope = scope
    return scope


def _run_cost(messages) -> float:
    total = 0.0
    for message in messages or []:
        if not isinstance(message, AssistantMessage):
            continue
        usage = getattr(message, "usage", None)
        cost = getattr(usage, "cost", None)
        total += float(getattr(cost, "total", 0.0) or 0.0)
    return round(total, 6)


def _terminal_error(messages) -> str | None:
    for message in reversed(messages or []):
        if isinstance(message, AssistantMessage) and message.error_message:
            return message.error_message
    return None


class RunRecorder:
    """Agent listener that mirrors one run into the project ledger."""

    def __init__(self, runtime) -> None:
        self._runtime = runtime
        self._record: RunRecord | None = None
        self._started_at: float | None = None

    def __call__(self, event: AgentEvent) -> None:
        try:
            if event.type == "agent_start":
                self._start(event)
            elif event.type == "agent_end":
                self._finish(event)
        except Exception:  # noqa: BLE001 - observability must never break a run
            # A ledger failure is already swallowed by RunLedger._append; this
            # guard covers scope/session shape surprises from embedders.
            self._record = None
            self._started_at = None
            self._runtime.current_run_id = None

    def _start(self, event: AgentEvent) -> None:
        scope = dict(getattr(self._runtime, "run_scope", {}) or {})
        session = getattr(self._runtime, "session", None)
        ledger = getattr(self._runtime, "run_ledger", None)
        if ledger is None:
            return
        self._record = ledger.start(
            kind=str(scope.get("kind") or "coding"),
            agent_id=str(scope.get("agentId") or "main"),
            session=str(getattr(session, "path", "") or ""),
            work_item_id=scope.get("workItemId"),
            parent_run_id=scope.get("parentRunId"),
            task_id=scope.get("taskId"),
            label=str(
                scope.get("label")
                or (Path(session.path).stem if session is not None else "")
            ),
        )
        self._started_at = time.monotonic()
        self._runtime.current_run_id = self._record.id

    def _finish(self, event: AgentEvent) -> None:
        record, self._record = self._record, None
        started, self._started_at = self._started_at, None
        ledger = getattr(self._runtime, "run_ledger", None)
        if record is None or ledger is None:
            return
        duration_ms = (
            None if started is None else round((time.monotonic() - started) * 1000)
        )
        status = status_for_termination(event.termination)
        ledger.finish(
            record,
            status=status,
            termination=event.termination,
            turns=event.turns,
            cost=_run_cost(event.new_messages),
            error=(
                _terminal_error(event.new_messages)
                if status in ("failed", "aborted")
                else None
            ),
            duration_ms=duration_ms,
        )
        self._runtime.current_run_id = None

    def relabel(
        self,
        *,
        status: str,
        termination: str | None = None,
        error: str | None = None,
        run_id: str | None = None,
    ) -> None:
        """Re-state how a run ended, for a layer that knows better than the kernel.

        ``status_for_termination`` maps the kernel's vocabulary only; a value it
        does not recognize — a wall-clock timeout the harness imposed — becomes
        ``done``. The owning layer appends one corrective terminal row for the
        same run id instead of teaching the shared mapping a private word, and
        the ledger's "last row per id wins" rule makes it stick.

        Best-effort by contract: an observability failure must never break the
        run that produced it.
        """
        try:
            ledger = getattr(self._runtime, "run_ledger", None)
            target = run_id or getattr(self._runtime, "current_run_id", None)
            if ledger is None or not target:
                return
            row = ledger.get(target)
            if row is None:
                return
            ledger.finish(
                row,
                status=status,
                termination=termination,
                error=error,
                duration_ms=row.duration_ms,
            )
        except Exception:  # noqa: BLE001 - observability never breaks a run
            _log.debug("run relabel failed", exc_info=True)
