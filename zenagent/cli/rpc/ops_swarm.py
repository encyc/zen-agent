"""The ``swarm`` wire handlers (mixin of RpcHost).

Swarm frames are global: they carry ``swarm: true`` and no project/session tag,
because one room spans every resident slot. Approval and ask replies find the
manager by the ``swarm:`` request-id prefix, the same prefix-routed contract
the per-slot servers use.
"""

from __future__ import annotations

import logging

from zenagent.app.swarm.manager import SwarmManager
from zenagent.cli.rpc.errors import Refusal, error_frame, failure_frame

#: Server-side diagnostics; raw exception detail never crosses the wire.
logger = logging.getLogger(__name__)


def _need(request: dict, key: str) -> str:
    value = request.get(key)
    if not isinstance(value, str) or not value.strip():
        raise Refusal(f"swarm op needs {key}")
    return value


class SwarmOps:
    """Mixin: see host.py for the composed RpcHost."""

    #: Built on the first swarm request; ``RpcHost.close`` disposes it.
    _swarm: SwarmManager | None
    _swarm_started: bool

    async def _swarm_op(self, request: dict, request_id) -> None:
        op = request.get("op")
        label = str(op or "")
        try:
            manager = await self._swarm_manager()
            if op == "list":
                payload = {
                    "roles": manager.roles(),
                    "agents": manager.agents_state(),
                    "tasks": manager.tasks(),
                    "messages": manager.messages(request.get("since")),
                    "warnings": list(manager.warnings),
                }
            elif op == "post":
                text = request.get("text")
                if not isinstance(text, str) or not text.strip():
                    raise Refusal("post needs text")
                tasks = await manager.post(text)
                payload = {"tasks": [manager.task_view(task) for task in tasks]}
            elif op == "cancel":
                task_id = _need(request, "taskId")
                payload = {
                    "taskId": task_id,
                    "cancelled": await manager.cancel(task_id),
                }
            elif op == "retry":
                task_id = _need(request, "taskId")
                task = await manager.retry(task_id)
                payload = {
                    "taskId": task_id,
                    "retried": task is not None,
                    "task": manager.task_view(task) if task is not None else None,
                }
            elif op == "reset":
                agent_id = _need(request, "agentId")
                payload = {
                    "agentId": agent_id,
                    "reset": await manager.reset(agent_id),
                }
            elif op == "roles":
                payload = await self._swarm_roles(manager, request)
            elif op == "reload":
                notes = await manager.reload()
                payload = {
                    "roles": manager.roles(),
                    "notes": notes,
                    "warnings": list(manager.warnings),
                }
            else:
                raise Refusal(f"unknown swarm op: {op}")
            self._send({"type": "result", "id": request_id, "ok": True, **payload})
        except Refusal as exc:
            self._send(
                error_frame(
                    request_id,
                    str(exc),
                    exc.code or "swarm_invalid",
                    {"op": label},
                )
            )
        except Exception as exc:
            logger.exception("swarm op failed")
            self._send(
                failure_frame(
                    request_id,
                    code="swarm_failed",
                    message="swarm failed",
                    exc=exc,
                    op=label,
                )
            )

    async def _swarm_manager(self) -> SwarmManager:
        if self._swarm is None:
            self._swarm = SwarmManager(write_frame=self._send)
        if not self._swarm_started:
            await self._swarm.start()
            self._swarm_started = True
        return self._swarm

    async def _swarm_roles(self, manager: SwarmManager, request: dict) -> dict:
        action = request.get("action") or "list"
        if action == "list":
            return {"roles": manager.roles(), "warnings": list(manager.warnings)}
        # `id` is the request-correlation id every RPC carries; the role's own
        # id travels as `roleId` so creating a role cannot shadow the answer.
        role_id = _need(request, "roleId")
        if action in ("create", "update"):
            manifest = request.get("manifest")
            if not isinstance(manifest, dict):
                raise Refusal("roles create/update need manifest")
            await manager.update_role(role_id, manifest)
        elif action == "delete":
            await manager.delete_role(role_id)
        else:
            raise Refusal(f"unknown roles action: {action}")
        return {"roles": manager.roles(), "warnings": list(manager.warnings)}

    def _resolve_swarm_prompt(self, request: dict, kind: str) -> bool:
        """Answer an approval/ask whose id names the swarm, not a slot."""
        request_id = request.get("request_id")
        if self._swarm is None:
            self._send(
                {
                    "type": "error",
                    "id": request.get("id"),
                    "message": f"no pending {kind}: {request_id}",
                    "code": "swarm_unavailable",
                }
            )
            return True
        if kind == "approval":
            resolved = self._swarm.resolve_approval(
                str(request_id), request.get("decision")
            )
        else:
            resolved = self._swarm.resolve_ask(
                str(request_id), request.get("answers") or []
            )
        if not resolved:
            self._send(
                {
                    "type": "error",
                    "id": request.get("id"),
                    "message": f"no pending {kind}: {request_id}",
                }
            )
            return True
        self._send({"type": "result", "id": request.get("id"), "ok": True})
        return True
