"""The host-level ``workspace`` mode: the project's backend mode fact."""

from __future__ import annotations

from pathlib import Path

from zenagent.app.workspace_state import (
    WORKSPACE_MODES,
    load_workspace,
    set_workspace_mode,
)
from zenagent.cli.rpc.errors import Refusal, error_frame, failure_frame
from zenagent.project import find_project_root

__all__ = ["WorkspaceOps"]


class WorkspaceOps:
    """Mixin: ``workspace`` ops of the composed :class:`~.host.RpcHost`."""

    async def _workspace_op(self, request: dict, request_id) -> None:
        op = request.get("op") or "get"
        label = str(op)
        try:
            root = self._workspace_root(request)
            if op == "get":
                state = load_workspace(root)
                self._send(self._workspace_answer(request_id, root, state))
                return
            if op == "set":
                mode = request.get("mode")
                if not isinstance(mode, str) or mode not in WORKSPACE_MODES:
                    raise Refusal(
                        f"unknown workspace mode: {mode}", code="workspace_invalid"
                    )
                router = request.get("routerSession")
                if router is not None and not isinstance(router, str):
                    raise Refusal(
                        "routerSession must be a string", code="workspace_invalid"
                    )
                state = set_workspace_mode(root, mode, router_session=router)
                self._send(
                    {
                        "type": "workspace",
                        "project": str(root),
                        "mode": state.mode,
                        "updatedAt": state.updated_at,
                        "routerSession": state.router_session,
                    }
                )
                self._send(self._workspace_answer(request_id, root, state))
                return
            raise Refusal(f"unknown workspace op: {op}", code="workspace_invalid")
        except Refusal as exc:
            self._send(
                error_frame(
                    request_id,
                    str(exc),
                    exc.code or "workspace_invalid",
                    {"op": label},
                )
            )
        except Exception as exc:  # noqa: BLE001 - a bad op is a result, not a crash
            self._send(
                failure_frame(
                    request_id,
                    code="workspace_failed",
                    message="workspace failed",
                    exc=exc,
                    op=label,
                )
            )

    @staticmethod
    def _workspace_answer(request_id, root: Path, state) -> dict:
        return {
            "type": "result",
            "id": request_id,
            "ok": True,
            "project": str(root),
            "mode": state.mode,
            "updatedAt": state.updated_at,
            "routerSession": state.router_session,
        }

    def _workspace_root(self, request: dict) -> Path:
        project = request.get("project")
        if isinstance(project, str) and project.strip():
            return find_project_root(project)
        active = getattr(self, "_active", None)
        if active is not None:
            return find_project_root(active.server.project)
        return find_project_root(self._options.root)
