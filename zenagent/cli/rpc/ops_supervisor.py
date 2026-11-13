"""Host-level ``supervisor`` queries and dispatch.

One project has one live-agent registry; this is the mode-agnostic view of it.
Dispatch is one entry too: the mode's dispatcher is mounted on the supervisor
while that orchestrator is alive, so the wire shape does not fork per mode.
"""

from __future__ import annotations

from pathlib import Path

from zenagent.app.dispatch import DispatchUnavailable
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
                approval_id = request.get("requestId")
                decision = request.get("decision")
                if not isinstance(approval_id, str) or not approval_id:
                    raise Refusal("approve needs requestId", code="supervisor_invalid")
                if decision not in ("allow", "always", "deny"):
                    raise Refusal(
                        "decision must be allow|always|deny",
                        code="supervisor_invalid",
                    )
                router = getattr(self, "_approvals", None)
                if router is None or not router.resolve(approval_id, str(decision)):
                    raise Refusal("no pending approval", code="approval_missing")
                self._send({"type": "result", "id": request_id, "ok": True})
                return
            if op == "dispatch":
                await self._supervisor_dispatch(request, request_id)
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

    async def _supervisor_dispatch(self, request: dict, request_id) -> None:
        root = self._supervisor_root(request)
        mode = request.get("mode") or load_workspace(root).mode
        if mode == "coding":
            raise Refusal(
                "coding is one session; prompt it instead of dispatching",
                code="dispatch_not_orchestrated",
            )
        if not isinstance(mode, str) or mode not in ("zen", "swarm"):
            raise Refusal(f"unknown dispatch mode: {mode}", code="dispatch_invalid")
        prompt = request.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise Refusal("dispatch needs prompt", code="supervisor_invalid")
        payload: dict = {"prompt": prompt}
        if mode == "zen":
            payload["session"] = self._zen_dispatch_target(root, request)
        else:
            role = request.get("role")
            if role is not None:
                if not isinstance(role, str) or not role.strip():
                    raise Refusal(
                        "role must be a non-empty string",
                        code="supervisor_invalid",
                    )
                payload["role"] = role
            ref = request.get("ref")
            if ref is not None:
                if not isinstance(ref, str) or not ref:
                    raise Refusal("ref must be a message id", code="supervisor_invalid")
                payload["ref"] = ref
            images = request.get("images")
            if images is not None:
                if not isinstance(images, list) or not all(
                    isinstance(item, str) for item in images
                ):
                    raise Refusal(
                        "images must be a list of file paths",
                        code="supervisor_invalid",
                    )
                payload["images"] = images
        try:
            result = await self._supervisor(root).dispatch(mode, **payload)
        except DispatchUnavailable as exc:
            raise Refusal(str(exc), code="dispatch_unavailable") from exc
        except ValueError as exc:
            raise Refusal(str(exc), code="dispatch_rejected") from exc
        self._send(
            {"type": "result", "id": request_id, "ok": True, "mode": mode, **result}
        )

    def _zen_dispatch_target(self, root: Path, request: dict) -> str:
        """Resolve the worker session a zen dispatch acts on."""
        session = request.get("session")
        if session is not None and not isinstance(session, str):
            raise Refusal("session must be a path", code="supervisor_invalid")
        agent = request.get("agent") or request.get("agentId")
        if agent is not None and not isinstance(agent, str):
            raise Refusal("agent must be a key", code="supervisor_invalid")
        resolved = ""
        if agent:
            handle = self._supervisor(root).get(agent)
            if handle is None:
                raise Refusal("no live agent", code="supervisor_missing")
            if handle.profile.kind != "zen_worker":
                raise Refusal("agent is not a zen worker", code="dispatch_invalid")
            resolved = str(handle.session.path)
        if session:
            if resolved:
                wanted = Path(session).expanduser()
                if str(wanted) != resolved and wanted.stem != Path(resolved).stem:
                    raise Refusal(
                        "session and agent disagree", code="dispatch_invalid"
                    )
            else:
                resolved = session
        if not resolved:
            raise Refusal(
                "zen dispatch needs session or agent", code="dispatch_invalid"
            )
        return resolved

    def _supervisor_root(self, request: dict) -> Path:
        project = request.get("project")
        if isinstance(project, str) and project.strip():
            return find_project_root(project)
        active = getattr(self, "_active", None)
        if active is not None:
            return find_project_root(active.server.project)
        return find_project_root(self._options.root)
