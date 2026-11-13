"""Host-level ``supervisor`` queries.

One project has one live-agent registry; this is the mode-agnostic view of it.
The scheduling/dispatch ops still live in ``zen_fleet`` and ``swarm`` until the
Phase-5 adapters land, so this family is deliberately read-only for now.
"""

from __future__ import annotations

from pathlib import Path

from zenagent.app.workspace_state import load_workspace
from zenagent.cli.rpc.errors import Refusal, error_frame, failure_frame
from zenagent.project import find_project_root

__all__ = ["SupervisorOps"]


class SupervisorOps:
    """Mixin: ``supervisor`` ops of the composed :class:`~.host.RpcHost`."""

    async def _supervisor_op(self, request: dict, request_id) -> None:
        op = request.get("op") or "status"
        label = str(op)
        try:
            if op == "cancel":
                key = request.get("key") or request.get("agentId")
                if not isinstance(key, str) or not key:
                    raise Refusal("cancel needs key", code="supervisor_invalid")
                root = self._supervisor_root(request)
                handle = next(
                    (
                        item
                        for item in self._supervisor(root).handles()
                        if item.key == key or item.profile.id == key
                    ),
                    None,
                )
                if handle is None:
                    raise Refusal("no live agent", code="supervisor_missing")
                running = bool(getattr(handle.runtime.agent, "is_streaming", False))
                if running:
                    handle.runtime.agent.abort()
                self._send(
                    {
                        "type": "result",
                        "id": request_id,
                        "ok": True,
                        "key": handle.key,
                        "cancelled": running,
                    }
                )
                return
            if op == "approve":
                request_id = request.get("requestId")
                decision = request.get("decision")
                if not isinstance(request_id, str) or not request_id:
                    raise Refusal("approve needs requestId", code="supervisor_invalid")
                if decision not in ("allow", "always", "deny"):
                    raise Refusal(
                        "decision must be allow|always|deny",
                        code="supervisor_invalid",
                    )
                router = getattr(self, "_approvals", None)
                if router is None or not router.resolve(request_id, str(decision)):
                    raise Refusal("no pending approval", code="approval_missing")
                self._send({"type": "result", "id": request_id, "ok": True})
                return
            if op != "status":
                raise Refusal(f"unknown supervisor op: {op}", code="supervisor_invalid")
            root = self._supervisor_root(request)
            supervisor = self._supervisor(root)
            agents = supervisor.snapshots()
            approvals = getattr(self, "_approvals", None)
            self._send(
                {
                    "type": "result",
                    "id": request_id,
                    "ok": True,
                    "project": str(root),
                    "mode": load_workspace(root).mode,
                    "agents": agents,
                    "counts": {
                        "agents": len(agents),
                        "running": sum(
                            1 for agent in agents if agent["state"] == "running"
                        ),
                    },
                    "approvals": approvals.pending() if approvals else [],
                }
            )
        except Refusal as exc:
            self._send(
                error_frame(
                    request_id,
                    str(exc),
                    exc.code or "supervisor_invalid",
                    {"op": label},
                )
            )
        except Exception as exc:  # noqa: BLE001 - a bad op is a result, not a crash
            self._send(
                failure_frame(
                    request_id,
                    code="supervisor_failed",
                    message="supervisor failed",
                    exc=exc,
                    op=label,
                )
            )

    def _supervisor_root(self, request: dict) -> Path:
        project = request.get("project")
        if isinstance(project, str) and project.strip():
            return find_project_root(project)
        active = getattr(self, "_active", None)
        if active is not None:
            return find_project_root(active.server.project)
        return find_project_root(self._options.root)
