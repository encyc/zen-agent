"""Host-level ``supervisor`` queries.

One project has one live-agent registry; this is the mode-agnostic view of it.
The scheduling/dispatch ops still live in ``zen_fleet`` and ``swarm`` until the
Phase-5 adapters land, so this family is deliberately read-only for now.
"""

from __future__ import annotations

from pathlib import Path

from zenagent.cli.rpc.errors import Refusal, error_frame, failure_frame
from zenagent.project import find_project_root

__all__ = ["SupervisorOps"]


class SupervisorOps:
    """Mixin: ``supervisor`` ops of the composed :class:`~.host.RpcHost`."""

    async def _supervisor_op(self, request: dict, request_id) -> None:
        op = request.get("op") or "status"
        label = str(op)
        try:
            if op != "status":
                raise Refusal(f"unknown supervisor op: {op}", code="supervisor_invalid")
            root = self._supervisor_root(request)
            supervisor = self._supervisor(root)
            self._send(
                {
                    "type": "result",
                    "id": request_id,
                    "ok": True,
                    "project": str(root),
                    "agents": supervisor.snapshots(),
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
