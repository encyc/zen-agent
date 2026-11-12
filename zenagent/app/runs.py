"""The project's run ledger: one append-only record per agent run.

The session file records *messages*; the ledger records *runs*. That is the
difference the orchestration layers kept re-inventing: coding kept run state in
``ChatController``, zen in ``WorkerHandle`` and swarm in ``TaskRecord``. This
module owns the shared vocabulary and the durable side table they can all write.

Storage is a JSONL file under the project's machine-local state directory::

    ~/.zenagent/projects/<key>/runs.jsonl

One line per state transition, last line per ``runId`` wins. A torn tail is
skipped with a warning, exactly like the room and session logs. Writes are
best-effort: losing a ledger line must never abort an agent run.
"""

from __future__ import annotations

import json
import logging
import secrets
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path

__all__ = [
    "RUN_KINDS",
    "RUN_STATUSES",
    "RunLedger",
    "RunRecord",
    "status_for_termination",
]

_log = logging.getLogger(__name__)

#: What produced the run. ``subagent`` is reserved for the task tool's children.
RUN_KINDS = ("coding", "zen_router", "zen_worker", "swarm", "subagent")

#: Terminal and in-flight states. ``aborted`` is a cooperative stop; a layer
#: that knows the user cancelled (swarm) may record ``cancelled`` instead.
RUN_STATUSES = ("running", "done", "failed", "aborted", "cancelled", "interrupted")

#: Terminations the kernel treats as a normal finish.
_DONE_TERMINATIONS = frozenset({"stop", "tool_terminate"})
#: Terminations that mean the run ended without delivering its answer.
_FAILED_TERMINATIONS = frozenset(
    {"max_turns", "max_cost", "error", "no_stream", "length"}
)

_WRITE_LOCK = threading.Lock()
_RECORD_VERSION = 1


def _now() -> int:
    return int(time.time())


def _new_id() -> str:
    return f"r_{time.time_ns():x}{secrets.token_hex(4)}"


def status_for_termination(termination: str | None) -> str:
    """Map the kernel's termination vocabulary to a ledger status.

    The mapping is deliberately conservative: only the two normal endings are
    ``done``; provider failures and budget stops are ``failed``; a cooperative
    abort is ``aborted`` and can be re-labelled by the owning layer.
    """
    if termination in _DONE_TERMINATIONS:
        return "done"
    if termination == "aborted":
        return "aborted"
    if termination in _FAILED_TERMINATIONS:
        return "failed"
    return "done"


@dataclass(frozen=True)
class RunRecord:
    """One agent run, as stored in the ledger."""

    id: str
    kind: str
    status: str
    started_at: int
    updated_at: int
    ended_at: int | None = None
    duration_ms: int | None = None
    agent_id: str = ""
    session: str = ""
    work_item_id: str | None = None
    parent_run_id: str | None = None
    task_id: str | None = None
    label: str = ""
    termination: str | None = None
    turns: int | None = None
    cost: float = 0.0
    error: str | None = None

    def to_json(self) -> dict:
        """The wire/disk shape (camelCase, like the room records)."""
        return {
            "type": "run",
            "version": _RECORD_VERSION,
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "startedAt": self.started_at,
            "updatedAt": self.updated_at,
            "endedAt": self.ended_at,
            "durationMs": self.duration_ms,
            "agentId": self.agent_id,
            "session": self.session,
            "workItemId": self.work_item_id,
            "parentRunId": self.parent_run_id,
            "taskId": self.task_id,
            "label": self.label,
            "termination": self.termination,
            "turns": self.turns,
            "cost": self.cost,
            "error": self.error,
        }

    @classmethod
    def from_json(cls, raw: dict) -> RunRecord | None:
        """Parse one line; ``None`` for anything that is not a run record."""
        if not isinstance(raw, dict) or raw.get("type") != "run":
            return None
        try:
            run_id = str(raw["id"])
        except (KeyError, TypeError):
            return None
        if not run_id:
            return None
        return cls(
            id=run_id,
            kind=str(raw.get("kind") or "coding"),
            status=str(raw.get("status") or "running"),
            started_at=int(raw.get("startedAt") or 0),
            updated_at=int(raw.get("updatedAt") or raw.get("startedAt") or 0),
            ended_at=(
                int(raw["endedAt"]) if raw.get("endedAt") is not None else None
            ),
            duration_ms=(
                int(raw["durationMs"]) if raw.get("durationMs") is not None else None
            ),
            agent_id=str(raw.get("agentId") or ""),
            session=str(raw.get("session") or ""),
            work_item_id=(
                str(raw["workItemId"]) if raw.get("workItemId") is not None else None
            ),
            parent_run_id=(
                str(raw["parentRunId"]) if raw.get("parentRunId") is not None else None
            ),
            task_id=str(raw["taskId"]) if raw.get("taskId") is not None else None,
            label=str(raw.get("label") or ""),
            termination=(
                str(raw["termination"]) if raw.get("termination") is not None else None
            ),
            turns=int(raw["turns"]) if raw.get("turns") is not None else None,
            cost=float(raw.get("cost") or 0.0),
            error=str(raw["error"]) if raw.get("error") is not None else None,
        )


class RunLedger:
    """Append-only run records for one project.

    Instances are cheap and stateless between calls; several runtimes of the
    same project can each own one, all appending to the same file. Reads replay
    the file so a fresh process sees exactly what the previous one wrote.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.warnings: list[str] = []

    # -- writes -------------------------------------------------------------

    def start(
        self,
        *,
        kind: str,
        agent_id: str = "",
        session: str = "",
        work_item_id: str | None = None,
        parent_run_id: str | None = None,
        task_id: str | None = None,
        label: str = "",
    ) -> RunRecord:
        """Open a run and append its ``running`` record."""
        now = _now()
        record = RunRecord(
            id=_new_id(),
            kind=kind,
            status="running",
            started_at=now,
            updated_at=now,
            agent_id=agent_id,
            session=session,
            work_item_id=work_item_id,
            parent_run_id=parent_run_id,
            task_id=task_id,
            label=label,
        )
        self._append(record)
        return record

    def finish(
        self,
        record: RunRecord,
        *,
        status: str,
        termination: str | None = None,
        turns: int | None = None,
        cost: float | None = None,
        error: str | None = None,
        duration_ms: int | None = None,
    ) -> RunRecord:
        """Append the terminal record for ``record`` and return the update."""
        finished = replace(
            record,
            status=status,
            updated_at=_now(),
            ended_at=_now(),
            duration_ms=duration_ms,
            termination=termination,
            turns=turns,
            cost=round(float(cost or 0.0), 6),
            error=error,
        )
        self._append(finished)
        return finished

    def _append(self, record: RunRecord) -> None:
        line = json.dumps(record.to_json(), ensure_ascii=False, default=str)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with _WRITE_LOCK, self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
                handle.flush()
        except OSError as exc:
            # Observability is best-effort; the run itself owns the truth.
            self.warnings.append(f"run ledger write failed: {exc}")
            _log.warning("run ledger write failed: %s", exc)

    # -- reads --------------------------------------------------------------

    def load(self) -> list[RunRecord]:
        """Replay the file, last record per id wins, ordered by start time."""
        self.warnings = []
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        except OSError as exc:
            self.warnings.append(f"run ledger read failed: {exc}")
            return []
        records: dict[str, RunRecord] = {}
        for number, line in enumerate(lines, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                self.warnings.append(f"run ledger line {number} is not JSON")
                continue
            record = RunRecord.from_json(raw)
            if record is None:
                self.warnings.append(f"run ledger line {number} is not a run")
                continue
            records[record.id] = record
        return sorted(records.values(), key=lambda item: (item.started_at, item.id))

    def get(self, run_id: str) -> RunRecord | None:
        return {record.id: record for record in self.load()}.get(run_id)

    def list(
        self,
        *,
        kind: str | None = None,
        agent: str | None = None,
        work_item: str | None = None,
        session: str | None = None,
        status: str | None = None,
        since: int | None = None,
        limit: int = 100,
    ) -> list[RunRecord]:
        """Filter the ledger; ``limit`` caps the newest-first result."""
        rows = self.load()
        if kind is not None:
            rows = [row for row in rows if row.kind == kind]
        if agent is not None:
            rows = [row for row in rows if row.agent_id == agent]
        if work_item is not None:
            rows = [row for row in rows if row.work_item_id == work_item]
        if session is not None:
            rows = [row for row in rows if row.session == session]
        if status is not None:
            rows = [row for row in rows if row.status == status]
        if since is not None:
            rows = [row for row in rows if row.started_at >= since]
        rows.sort(key=lambda item: (item.started_at, item.id), reverse=True)
        return rows[: max(0, int(limit))]
