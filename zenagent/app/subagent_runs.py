"""Record ``task``-tool children in the project run ledger.

A subagent has no session of its own, but it is still one of the four agent
lifecycles. Its ledger row names the parent run and carries the parent's
session, so the normal session lease answers "is this child still alive?" and
crash reconciliation closes it with the parent.
"""

from __future__ import annotations

from typing import Any

__all__ = ["SubagentRunSink"]

_STATUS = {
    "ok": "done",
    "error": "failed",
    "aborted": "aborted",
    "truncated": "failed",
}


class SubagentRunSink:
    """Small adapter the task tool calls around one child dispatch."""

    def __init__(self, runtime) -> None:
        self.runtime = runtime

    def start(self, name: str):
        ledger = getattr(self.runtime, "run_ledger", None)
        if ledger is None:
            return None
        session = getattr(self.runtime, "session", None)
        return ledger.start(
            kind="subagent",
            agent_id=name,
            session=str(getattr(session, "path", "") or ""),
            parent_run_id=getattr(self.runtime, "current_run_id", None),
            label=name,
        )

    def finish(self, record: Any, details: dict | None, error: str) -> None:
        """Close a dispatched child; never raise into the task tool."""
        if record is None:
            return
        ledger = getattr(self.runtime, "run_ledger", None)
        if ledger is None:
            return
        payload = details or {}
        status = _STATUS.get(str(payload.get("status") or ""), "")
        if not status:
            status = "failed" if error else "aborted"
        ledger.finish(
            record,
            status=status,
            termination=(
                payload.get("termination") or payload.get("reason") or error or None
            ),
            turns=payload.get("turns"),
            cost=payload.get("cost"),
            error=payload.get("error") or error or None,
            duration_ms=payload.get("duration_ms"),
        )
