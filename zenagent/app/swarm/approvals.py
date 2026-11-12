"""Approval and ask callbacks for swarm run units.

Extracted from ``SwarmManager`` to keep that file under its architecture LOC
budget. The mixin owns the pending-approval futures; the manager supplies the
agents, runners and the wire callback it already has.

An approval is per run unit: the request carries the work item so a human can
tell which job is asking, and a timeout aborts that unit's run rather than
leaving the tool waiting forever.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from zenagent.app.swarm.instances import AgentKey

if TYPE_CHECKING:
    from zenagent.app.swarm.manager import SwarmAgent

__all__ = ["APPROVAL_TIMEOUT_S", "SwarmApprovals"]

APPROVAL_TIMEOUT_S = 120.0


class SwarmApprovals:
    """Pending approvals and the two broker callbacks wired into runtimes."""

    _approvals: dict[str, asyncio.Future]
    _approval_seq: int
    _approval_prefix: str
    _write: Callable[[dict], None]
    _system_note: Callable[[str], object]
    agents: dict[AgentKey, "SwarmAgent"]
    runners: dict[AgentKey, Any]

    def resolve_approval(self, request_id: str, decision: str) -> bool:
        if decision not in ("allow", "always", "deny"):
            return False
        future = self._approvals.get(request_id)
        if future is None or future.done():
            return False
        future.set_result(decision)
        return True

    def resolve_ask(self, request_id: str, answers: list) -> bool:
        # v1 answers asks immediately (unattended semantics); nothing pends.
        return False

    def _confirm_callback(self, key: AgentKey):
        role_id = key[0]
        work_item_id = key[1]

        async def confirm(tool_name: str, arguments: dict, verdict=None) -> str:
            agent = self.agents.get(key)
            task_id = agent.current_task if agent is not None else None
            request_id = f"{self._approval_prefix}a{self._approval_seq}"
            self._approval_seq += 1
            event: dict = {
                "type": "approval_request",
                "swarm": True,
                "agent": role_id,
                "workItem": work_item_id,
                "task": task_id,
                "request_id": request_id,
                "tool_name": tool_name,
                "args": json.dumps(arguments, ensure_ascii=False, default=str),
            }
            if verdict is not None:
                event["kind"] = verdict.kind
                event["risk"] = verdict.risk
                event["target"] = verdict.target
                event["reason"] = verdict.reason
            future: asyncio.Future = asyncio.get_running_loop().create_future()
            self._approvals[request_id] = future
            self._write({"type": "event", "event": event})
            try:
                return await asyncio.wait_for(future, APPROVAL_TIMEOUT_S)
            except TimeoutError:
                runner = self.runners.get(key)
                if runner is not None:
                    runner.abort_outcome = ("failed", "approval_timeout")
                if agent is not None:
                    agent.agent.abort()
                return "deny"
            finally:
                self._approvals.pop(request_id, None)

        return confirm

    def _ask_handler(self, role_id: str):
        async def ask(questions: list[dict]):
            summary = ""
            if questions:
                summary = str(questions[0].get("question") or "")[:120]
            self._system_note(
                f"@{role_id} asked: {summary} — auto-dismissed (unattended)"
            )
            return [None for _ in questions]

        return ask
