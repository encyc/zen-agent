"""One typed registry for pending approvals across slots and swarm rooms.

Historically the host routed an approval reply by parsing the request-id
prefix (``<project>:swarm:`` vs a slot key). That made correlation a string
convention. The router keeps the same wire ids for compatibility but stores
the owner and the run/agent/work-item facts explicitly, so the host resolves a
reply by lookup rather than by prefix arithmetic.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

__all__ = [
    "ApprovalEntry",
    "ApprovalRouter",
    "approval_event",
    "approval_event_for",
    "register_approval",
]


@dataclass
class ApprovalEntry:
    request_id: str
    project: str = ""
    agent_id: str = ""
    run_id: str | None = None
    work_item_id: str | None = None
    task_id: str | None = None
    future: asyncio.Future | None = field(default=None, repr=False)


class ApprovalRouter:
    """Pending approvals keyed by the wire request id."""

    def __init__(self) -> None:
        self._entries: dict[str, ApprovalEntry] = {}

    def register(self, entry: ApprovalEntry) -> None:
        if entry.request_id in self._entries:
            raise ValueError(f"duplicate approval: {entry.request_id}")
        self._entries[entry.request_id] = entry

    def resolve(self, request_id: str, decision: str) -> bool:
        entry = self._entries.get(request_id)
        if entry is None or entry.future is None or entry.future.done():
            return False
        entry.future.set_result(decision)
        return True

    def forget(self, request_id: str) -> None:
        self._entries.pop(request_id, None)

    def pending(self) -> list[dict]:
        return [
            {
                "requestId": entry.request_id,
                "project": entry.project,
                "agentId": entry.agent_id,
                "runId": entry.run_id,
                "workItemId": entry.work_item_id,
                "taskId": entry.task_id,
            }
            for entry in self._entries.values()
        ]

    def clear(self) -> None:
        entries = list(self._entries.values())
        self._entries.clear()
        for entry in entries:
            if entry.future is not None and not entry.future.done():
                entry.future.set_result("deny")


def approval_event(
    request_id: str,
    tool_name: str,
    args: str,
    verdict=None,
    *,
    run_id: str | None = None,
    agent_id: str = "main",
    work_item_id: str | None = None,
    task_id: str | None = None,
) -> dict:
    """The wire event for one approval request, with run correlation on it."""
    event: dict = {
        "type": "approval_request",
        "request_id": request_id,
        "tool_name": tool_name,
        "args": args,
        "agentId": agent_id,
        "runId": run_id,
        "workItemId": work_item_id,
        "taskId": task_id,
    }
    if verdict is not None:
        event["kind"] = verdict.kind
        event["risk"] = verdict.risk
        event["target"] = verdict.target
        event["reason"] = verdict.reason
    return event


def approval_event_for(server, request_id: str, tool_name: str, args: str, verdict=None) -> dict:
    """Build the event from a live server/runtime (keeps server.py compact)."""
    runtime = getattr(server, "runtime", None)
    scope = getattr(runtime, "run_scope", {}) if runtime is not None else {}
    return approval_event(
        request_id,
        tool_name,
        args,
        verdict,
        run_id=getattr(runtime, "current_run_id", None) if runtime else None,
        agent_id=str(scope.get("agentId") or "main"),
        work_item_id=scope.get("workItemId"),
        task_id=scope.get("taskId"),
    )


def register_approval(router, server, request_id: str, future) -> None:
    """Register one slot approval with its project/run correlation."""
    runtime = getattr(server, "runtime", None)
    scope = getattr(runtime, "run_scope", {}) if runtime is not None else {}
    router.register(
        ApprovalEntry(
            request_id=request_id,
            project=str(getattr(server, "project", "") or ""),
            agent_id=str(scope.get("agentId") or "main"),
            run_id=getattr(runtime, "current_run_id", None) if runtime else None,
            work_item_id=scope.get("workItemId"),
            task_id=scope.get("taskId"),
            future=future,
        )
    )
