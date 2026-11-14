"""The ``zen_fleet`` wire handlers (mixin of RpcHost).

Zen is per project and **host-level**: a project in zen has a dedicated router
slot that carries the Router session and its fleet, while the coding slot keeps
its own session — and any run on it — untouched. That is the sibling-slot
mechanism ``session resume`` already uses when the target session lives in a
busy slot, so entering zen never aborts a run and never refuses a busy project.

Entering zen opens the router slot (``latest_router`` or a fresh session) and
attaches the fleet to it; leaving zen closes the fleet and hands the foreground
back to the slot that was on screen before, closing the router slot unless it
is still running a turn (then it stays resident, and its session stays
readable). Roster changes are pushed as ``zen_fleet`` frames so a GUI's worker
list stays live without polling.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from zenagent.agent.session import Session
from zenagent.app.agent_profile import AgentProfile
from zenagent.app.dispatch import ZenDispatcher
from zenagent.app.run_wiring import update_run_scope
from zenagent.app.views import TranscriptBuilder
from zenagent.app.workspace_state import load_workspace, set_workspace_mode
from zenagent.app.zen.fleet import ZenFleet
from zenagent.app.zen.roster import latest_router, set_role
from zenagent.app.zen.tools import register_router_surface
from zenagent.cli.rpc.errors import Refusal, error_frame, failure_frame
from zenagent.cli.rpc.ops_session import _optional_count, _page_views
from zenagent.config import load_user_config
from zenagent.project import find_project_root, project_key, project_sessions_dir

__all__ = ["ZenOps"]


class ZenOps:
    """Mixin: the zen ops of the composed :class:`~.host.RpcHost`."""

    #: Per project key: the session the foreground showed before zen took it
    #: (restored on disable). Owned by the host instance (see its __init__).
    _zen_pre: dict[str, str]

    # -- dispatch -----------------------------------------------------------

    async def _zen_op(self, request: dict, request_id) -> None:
        op = request.get("op")
        label = str(op or "")
        try:
            root, key = self._zen_project(request)
            if op == "enable":
                await self._zen_enable(root, key, request_id)
            elif op == "disable":
                await self._zen_disable(root, key, request_id)
            elif op == "state":
                self._zen_state(root, key, request_id)
            elif op == "transcript":
                self._zen_transcript(request, request_id)
            else:
                raise Refusal(f"unknown zen_fleet op: {op}")
        except Refusal as exc:
            self._send(
                error_frame(
                    request_id,
                    str(exc),
                    exc.code or "zen_invalid",
                    {"op": label},
                )
            )
        except Exception as exc:  # noqa: BLE001 - a bad op is a result, not a crash
            self._send(
                failure_frame(
                    request_id,
                    code="zen_failed",
                    message="zen_fleet failed",
                    exc=exc,
                    op=label,
                )
            )

    def _zen_project(self, request: dict) -> tuple[Path, str]:
        """The project a zen op acts on: the named one, else the foreground."""
        raw = request.get("project")
        if isinstance(raw, str) and raw.strip():
            root = find_project_root(raw).resolve()
            return root, project_key(root)
        active = getattr(self, "_active", None)
        if active is None:
            raise Refusal("no project: open one first", code="no_project")
        root = Path(active.runtime.root).resolve()
        return root, project_key(root)

    def _zen_slot_for(self, key: str):
        """The project's router slot, or None while the project is in coding."""
        for slot in self._slots.values():
            if slot.project_key != key:
                continue
            if getattr(slot.runtime, "zen_fleet", None) is not None:
                return slot
        return None

    # -- enable / disable ---------------------------------------------------

    async def _zen_enable(self, root: Path, key: str, request_id) -> None:
        slot = self._zen_slot_for(key)
        if slot is not None:
            # Already zen: answer with the live state instead of rebuilding
            # (a rebuild would orphan the fleet and its workers).
            self._send(self._zen_answer(request_id, root, slot))
            return
        sessions_dir = project_sessions_dir(root)
        # A fresh project may never have had a session; the roster sidecar and
        # the router transcript need the directory to exist.
        sessions_dir.mkdir(parents=True, exist_ok=True)
        previous = self._active_by_project.get(key)
        router_file = _router_file(sessions_dir)
        slot = self._holder(root, str(router_file)) if router_file is not None else None
        if slot is None:
            # Always a sibling slot: the coding session (and its run) stays
            # where it is, which is the whole point of host-level zen.
            slot = await self._spawn(
                root,
                resume=str(router_file) if router_file is not None else None,
                base=key,
            )
        set_role(sessions_dir, Path(slot.runtime.session.path).stem, "router")
        if previous is not None and previous is not slot:
            self._zen_pre[key] = str(previous.runtime.session.path)
        else:
            self._zen_pre.pop(key, None)
        self._attach_fleet(root, slot)
        self._activate(slot)
        self._send_fleet_roster(root, slot)
        self._send(self._zen_answer(request_id, root, slot))

    async def _zen_disable(self, root: Path, key: str, request_id) -> None:
        slot = self._zen_slot_for(key)
        if slot is None:
            # Leaving zen twice is not an error: answer with where the
            # foreground already is.
            target = self._active_by_project.get(key)
            self._send_fleet_roster(root, target)
            self._send(self._zen_disabled(request_id, root, target))
            return
        await self._detach_fleet(slot)
        target = await self._restore_slot(root, key, slot)
        self._activate(target)
        if target is not slot and not slot.busy:
            # Nothing is running on the router: free the runtime. A busy one
            # stays resident so its turn can finish; LRU evicts it later.
            await self._drop(slot.key)
        self._send_fleet_roster(root, target)
        self._send(self._zen_disabled(request_id, root, target))

    async def _restore_slot(self, root: Path, key: str, router) -> object:
        """The slot the foreground returns to when zen ends."""
        current = self._active_by_project.get(key)
        if current is not None and current is not router:
            # The person browsed away from the router while zen was on (a
            # sibling slot holds that session): leaving zen keeps it on screen
            # instead of yanking the foreground back to the pre-zen session.
            self._zen_pre.pop(key, None)
            return current
        pre = self._zen_pre.pop(key, "")
        if pre:
            holder = self._holder(root, pre)
            if holder is not None:
                return holder
        sibling = next(
            (
                other
                for other in reversed(self._slots.values())
                if other.project_key == key and other is not router
            ),
            None,
        )
        if sibling is not None:
            return sibling
        if pre and Path(pre).exists():
            try:
                return await self._spawn(root, resume=pre, base=key)
            except Refusal:
                return router
        return router

    # -- fleet lifecycle ----------------------------------------------------

    def _attach_fleet(self, root: Path, slot) -> ZenFleet:
        """Build the project's fleet on ``slot`` and enable the router surface."""
        runtime = slot.runtime
        config = getattr(runtime, "user_config", None) or load_user_config()
        supervisor = self._supervisor(root) if hasattr(self, "_supervisor") else None
        fleet = ZenFleet(
            chat=slot.chat,
            root=Path(runtime.root),
            sessions_dir=project_sessions_dir(runtime.root),
            router_path=str(runtime.session.path),
            max_concurrent=config.zen_max_concurrent,
            on_change=lambda: self._send_fleet_roster(root, slot),
            confirm=slot.server.confirm,
            ask=slot.server.ask,
            mode=getattr(runtime, "mode", None),
            batch_window_s=config.zen_batch_window_s,
            run_timeout_s=config.task_timeout_s,
            report_limit=config.zen_report_limit,
            worker_mode=config.zen_worker_mode,
            supervisor=supervisor,
            # Workers are spawned agents: they take the same [task] budgets as
            # the task tool's children.
            max_cost=config.task_max_cost,
            max_turns=config.task_max_turns,
        )
        runtime.zen_fleet = fleet
        register_router_surface(runtime)
        set_workspace_mode(root, "zen", router_session=str(runtime.session.path))
        if supervisor is not None:
            supervisor.register(
                slot.key,
                profile=AgentProfile(id="router", kind="zen_router", label="router"),
                runtime=runtime,
                session=runtime.session,
            )
            supervisor.attach_dispatcher(ZenDispatcher(fleet))
        update_run_scope(runtime, kind="zen_router", agentId="router", label="router")
        # The role guard, the fleet tools and the router prompt note all read
        # ``zen_fleet`` at compose time, so one recompose is the whole attach.
        runtime._recompose()
        chat = slot.chat
        if chat is not None and hasattr(chat, "reload_transcript"):
            chat.reload_transcript(drop_pending=True)
        return fleet

    async def _detach_fleet(self, slot) -> None:
        """Stop the workers and drop the router surface back to coding."""
        fleet = getattr(slot.runtime, "zen_fleet", None)
        if fleet is not None:
            await fleet.close()
        slot.runtime.zen_fleet = None
        if load_workspace(Path(slot.runtime.root)).mode == "zen":
            set_workspace_mode(slot.runtime.root, "coding")
        if hasattr(self, "_supervisor"):
            supervisor = self._supervisor(Path(slot.runtime.root))
            supervisor.detach_dispatcher("zen")
            supervisor.register(
                slot.key,
                profile=AgentProfile(id=slot.key, kind="coding", label=slot.key),
                runtime=slot.runtime,
                session=slot.runtime.session,
            )
        update_run_scope(slot.runtime, kind="coding", agentId="main", label="")
        slot.runtime._recompose()

    # -- frames -------------------------------------------------------------

    def _zen_answer(self, request_id, root: Path, slot) -> dict:
        return {
            "type": "result",
            "id": request_id,
            "ok": True,
            "enabled": True,
            "project": str(root),
            "router": str(slot.runtime.session.path),
            "session": str(slot.runtime.session.path),
            "roster": slot.runtime.zen_fleet.roster(),
        }

    def _zen_disabled(self, request_id, root: Path, slot) -> dict:
        return {
            "type": "result",
            "id": request_id,
            "ok": True,
            "enabled": False,
            "project": str(root),
            "router": "",
            "session": str(slot.runtime.session.path) if slot is not None else "",
            "roster": [],
        }

    def _zen_state(self, root: Path, key: str, request_id) -> None:
        slot = self._zen_slot_for(key)
        if slot is not None:
            self._send(self._zen_answer(request_id, root, slot))
            return
        target = self._active_by_project.get(key)
        frame = self._zen_disabled(request_id, root, target)
        # A state answer is also a read of the project, so it names the
        # foreground session the client should be showing.
        self._send(frame)

    def _send_fleet_roster(self, root: Path, slot) -> None:
        """Push the current roster (empty when zen is off) as a live frame."""
        fleet = getattr(slot.runtime, "zen_fleet", None) if slot is not None else None
        frame: dict = {
            "type": "zen_fleet",
            "enabled": fleet is not None,
            "project": str(root),
            "roster": fleet.roster() if fleet is not None else [],
        }
        if slot is not None:
            frame["session"] = str(slot.runtime.session.path)
        self._send(frame)

    # -- transcript ---------------------------------------------------------

    def _zen_transcript(self, request: dict, request_id) -> None:
        """A read-only view of one session file (a worker's transcript)."""
        raw = request.get("path")
        if not isinstance(raw, str) or not raw.strip():
            raise Refusal("zen transcript needs path", code="zen_invalid")
        path = Path(raw).expanduser()
        if not path.exists():
            raise Refusal(f"no such session: {path.name}", code="zen_invalid")
        session = Session(path)
        messages = session.load()
        builder = TranscriptBuilder()
        builder.load(messages, list(session.records))
        views = [asdict(view) for view in builder.views]
        total = len(views)
        paged, has_more_before = _page_views(
            views,
            limit=_optional_count(request.get("limit"), minimum=1),
            before=_optional_count(request.get("before"), minimum=0),
        )
        self._send(
            {
                "type": "result",
                "id": request_id,
                "ok": True,
                "transcript": paged,
                "notices": [],
                "compactions": [],
                "hasMoreBefore": has_more_before,
                "total": total,
            }
        )


def _router_file(sessions_dir: Path) -> Path | None:
    """The project's last router transcript, when it still exists on disk."""
    stem = latest_router(sessions_dir)
    if not stem:
        return None
    path = sessions_dir / f"{stem}.jsonl"
    return path if path.exists() else None
