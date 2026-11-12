"""Wire a Runtime to the project's run ledger.

The recorder is attached by :func:`~zenagent.app.bootstrap.prepare` for every
frontend, and by the swarm manager for role runtimes (swarm deliberately builds
its runtimes without ``prepare``). Nothing here changes agent behaviour: an
``agent_start`` opens a ledger record and an ``agent_end`` closes it, both
best-effort.

The scope is a plain dict on the Runtime, set by the orchestration layer before
it prompts the agent::

    {"kind": "coding", "agentId": "main", "label": "..."}
    {"kind": "zen_router", "agentId": "router"}
    {"kind": "zen_worker", "agentId": "worker-a", "parentRunId": "r_...", "label": "..."}
    {"kind": "swarm", "agentId": "research", "workItemId": "w_...", "taskId": "t_..."}

A missing/unknown kind is recorded as ``coding`` so an embedder that never sets
a scope still gets a useful row.
"""

from __future__ import annotations

import time
from pathlib import Path

from zenagent import project
from zenagent.app.runs import RunLedger, RunRecord, status_for_termination
from zenagent.events import AgentEvent
from zenagent.types import AssistantMessage

__all__ = ["RunRecorder", "attach_run_recorder", "update_run_scope"]


def attach_run_recorder(runtime) -> RunLedger:
    """Attach one recorder to ``runtime.agent``; idempotent per Runtime."""
    existing = getattr(runtime, "run_ledger", None)
    if existing is not None:
        return existing
    ledger = RunLedger(project.project_dir(root=runtime.root) / "runs.jsonl")
    runtime.run_ledger = ledger
    runtime.run_scope = {
        "kind": "coding",
        "agentId": "main",
        "label": Path(runtime.session.path).stem,
    }
    runtime.current_run_id = None
    runtime.agent.subscribe(RunRecorder(runtime))
    return ledger


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
            label=str(scope.get("label") or ""),
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
