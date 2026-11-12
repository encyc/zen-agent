"""The ``swarm`` wire handlers (mixin of RpcHost).

Swarm is per project: one manager (room) per project key, each rooted at that
project's state dir. Every emitted frame carries ``swarm: true`` and
``project``, so the desktop routes it to the active room. Approval and ask
replies find their manager by the ``<project_key>:swarm:`` request-id prefix,
the same prefix-routed contract the per-slot servers use.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from pathlib import Path

from zenagent.app.swarm.manager import SwarmManager
from zenagent.app.swarm.roles import discover_roles
from zenagent.app.swarm.roster import copy_role, role_view
from zenagent.cli.rpc.codec import serialize_event
from zenagent.cli.rpc.errors import Refusal, error_frame, failure_frame
from zenagent.config import load_user_config
from zenagent.events import AgentEvent
from zenagent.project import find_project_root, project_key

#: Server-side diagnostics; raw exception detail never crosses the wire.
logger = logging.getLogger(__name__)


def _need(request: dict, key: str) -> str:
    value = request.get(key)
    if not isinstance(value, str) or not value.strip():
        raise Refusal(f"swarm op needs {key}")
    return value


def _swarm_key_from_request_id(request_id: str) -> str | None:
    key, sep, _ = request_id.partition(":swarm:")
    return key if sep else None


def _is_swarm_request_id(request_id: str) -> bool:
    return ":swarm:" in request_id or request_id.startswith("swarm:")


class SwarmOps:
    """Mixin: see host.py for the composed RpcHost."""

    #: One room per project key, built on that project's first swarm request.
    #: ``RpcHost.close`` disposes all of them; idle rooms beyond the resident
    #: cap are closed LRU.
    _swarm: OrderedDict[str, SwarmManager]

    async def _swarm_op(self, request: dict, request_id) -> None:
        op = request.get("op")
        label = str(op or "")
        try:
            root = self._swarm_root(request)
            manager = await self._swarm_manager(root)
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
                ref = request.get("ref")
                if ref is not None and not isinstance(ref, str):
                    raise Refusal("post ref must be a message id")
                images = request.get("images")
                if images is not None and (
                    not isinstance(images, list)
                    or not all(isinstance(item, str) for item in images)
                ):
                    raise Refusal("post images must be a list of file paths")
                try:
                    tasks = await manager.post(text, ref=ref, images=images)
                except ValueError as exc:
                    raise Refusal(str(exc)) from exc
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
            elif op == "transcript":
                task_id = _need(request, "taskId")
                payload = {
                    "taskId": task_id,
                    **manager.task_transcript(task_id),
                }
            elif op == "works":
                payload = self._swarm_works(manager, request)
            elif op == "roles":
                payload = await self._swarm_roles(manager, request, root)
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

    def _swarm_works(self, manager: SwarmManager, request: dict) -> dict:
        """The work item panel: list/detail plus the human's score and close."""
        action = request.get("action") or "list"
        if action == "list":
            return {"works": manager.works(), "adhoc": manager.adhoc_tasks()}
        work_id = _need(request, "workId")
        try:
            if action == "detail":
                return manager.work_detail(work_id)
            if action == "score":
                score = request.get("score")
                if score is not None and (
                    not isinstance(score, int) or isinstance(score, bool)
                ):
                    raise Refusal("score must be 1-5 or null")
                note = request.get("note")
                if note is not None and not isinstance(note, str):
                    raise Refusal("score note must be a string")
                return {"work": manager.work_view(manager.score_work(work_id, score, note))}
            if action == "close":
                score = request.get("score")
                if score is not None and (
                    not isinstance(score, int) or isinstance(score, bool)
                ):
                    raise Refusal("score must be 1-5 or null")
                note = request.get("note")
                if note is not None and not isinstance(note, str):
                    raise Refusal("close note must be a string")
                item = manager.close_work(work_id, score=score, note=note)
                return {"work": manager.work_view(item)}
            if action == "reopen":
                return {"work": manager.work_view(manager.reopen_work(work_id))}
            if action == "merge":
                target = _need(request, "targetId")
                item = manager.merge_works(work_id, target)
                return {"work": manager.work_view(item)}
            if action == "rename":
                item = manager.rename_work(work_id, _need(request, "title"))
                return {"work": manager.work_view(item)}
        except ValueError as exc:
            raise Refusal(str(exc)) from exc
        raise Refusal(f"unknown works action: {action}")

    async def _swarm_manager(self, root: Path) -> SwarmManager:
        """The project's room, built and started on first use.

        Frames are stamped with the project here, so no emission path can
        forget the tag.
        """
        key = project_key(root)
        manager = self._swarm.get(key)
        if manager is None:
            config = (
                getattr(self.runtime, "user_config", None)
                if getattr(self, "runtime", None) is not None
                else None
            ) or load_user_config()
            manager = SwarmManager(
                root=root,
                write_frame=lambda payload, r=root: self._send(
                    {**payload, "project": str(r)}
                ),
                event_sink=lambda role_id, task_id, event, work_item_id=None, r=root: (
                    self._send_role_event(role_id, task_id, event, r, work_item_id)
                ),
                max_concurrent=config.swarm_max_concurrent,
                max_chain_depth=config.swarm_max_chain_depth,
                max_chain_cost=config.swarm_max_chain_cost,
                runtime_idle_s=config.swarm_runtime_idle_s,
            )
            self._swarm[key] = manager
        else:
            self._swarm.move_to_end(key)
        if not manager.started:
            await manager.start()
        await self._evict_idle_rooms(keep=key)
        return manager

    async def _evict_idle_rooms(self, *, keep: str) -> None:
        """Close least-recently-used idle rooms beyond the resident budget.

        A room with a run in flight is never a victim (closing it would kill
        the run), and neither is the room the current request just asked for.
        """
        cap = max(1, self._max_slots)
        while len(self._swarm) > cap:
            victim = next(
                (
                    key
                    for key, manager in self._swarm.items()
                    if key != keep and not manager.has_active()
                ),
                None,
            )
            if victim is None:
                break  # every other resident room is busy: tolerate overshoot
            manager = self._swarm.pop(victim)
            await manager.close()

    def _swarm_root(self, request: dict) -> Path:
        """The project this request belongs to: named, active, else startup."""
        project = request.get("project")
        if isinstance(project, str) and project.strip():
            return find_project_root(project)
        if self._active is not None:
            return find_project_root(self._active.server.project)
        return find_project_root(self._options.root)

    def _send_role_event(
        self,
        role_id: str,
        task_id: str,
        event: AgentEvent,
        root: Path,
        work_item_id: str | None = None,
    ) -> None:
        """One role-run event tagged with its project: the desktop streams a
        task's detail from these (the session file only lands at run end)."""
        self._send(
            {
                "type": "swarm_event",
                "swarm": True,
                "project": str(root),
                "agent": role_id,
                "workItem": work_item_id,
                "task": task_id,
                "event": serialize_event(event),
            }
        )

    async def _swarm_roles(
        self, manager: SwarmManager, request: dict, root: Path
    ) -> dict:
        action = request.get("action") or "list"
        if action == "list":
            return {"roles": manager.roles(), "warnings": list(manager.warnings)}
        if action == "library":
            roles, warnings = discover_roles(None)
            return {
                "roles": [role_view(role) for role in roles],
                "warnings": list(warnings),
            }
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
        elif action == "copy":
            try:
                copy_role(role_id, root=root)
            except ValueError as exc:
                raise Refusal(str(exc)) from exc
            await manager.reload()
        else:
            raise Refusal(f"unknown roles action: {action}")
        return {"roles": manager.roles(), "warnings": list(manager.warnings)}

    def _resolve_swarm_prompt(self, request: dict, kind: str) -> bool:
        """Answer an approval/ask whose id names a project's swarm room."""
        request_id = request.get("request_id")
        key = _swarm_key_from_request_id(str(request_id))
        if key is None:
            key = project_key(self._swarm_root({}))
        manager = self._swarm.get(key)
        if manager is None:
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
            resolved = manager.resolve_approval(
                str(request_id), request.get("decision")
            )
        else:
            resolved = manager.resolve_ask(
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
