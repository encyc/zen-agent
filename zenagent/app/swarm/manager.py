"""SwarmManager: one runtime per (role, work item), and the room lifecycle.

A role is a *configuration*; a work item is a *job*. The run unit is the pair:
one agent may work two jobs, each with its own runtime and session. The manager owns what the runner deliberately does not: inbox sizes for the
router gates, the approval broker, the role lifecycle and the work item table.

Scheduling lives in :mod:`zenagent.app.swarm.schedule`, wire emission in
:mod:`zenagent.app.swarm.frames`, instance identity in
:mod:`zenagent.app.swarm.instances`.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from zenagent.agent.agent import Agent
from zenagent.agent.session import Session
from zenagent.app import agent_profile as agents
from zenagent.app.plugin_wiring import start_mcp
from zenagent.app.bootstrap import prepare
from zenagent.app.run_wiring import attach_swarm_run
from zenagent.app.runtime import Runtime, RuntimeOptions
from zenagent.app.swarm.approvals import APPROVAL_TIMEOUT_S, SwarmApprovals
from zenagent.app.swarm.events_tap import EventSink, subscribe_events
from zenagent.app.swarm.frames import SwarmFrames
from zenagent.app.swarm.instances import AgentKey, IdleTracker, agent_key
from zenagent.app.swarm.protocol import (
    PROTOCOL_TEXT,
    build_swarm_post_tool,
)
from zenagent.app.swarm.roles import (
    RoleManifest,
    discover_roles,
    legacy_room_warnings,
    project_state_root,
    role_session_path,
    sessions_dir,
    supports_vision,
)
from zenagent.app.swarm.room import (
    RoomMessage,
    RoomRef,
    SwarmStore,
    TaskRecord,
    _message_record,
    _task_record,
    is_terminal,
    parse_mentions,
)
from zenagent.app.swarm.roster import (
    remove_role_dir,
    role_signature,
    role_view,
    write_role,
)
from zenagent.app.swarm.router import (
    DEFAULT_MAX_CHAIN_COST,
    DEFAULT_MAX_CHAIN_DEPTH,
    Router,
)
from zenagent.app.swarm.runner import TaskRunner
from zenagent.app.swarm.schedule import SwarmSchedule
from zenagent.app.swarm.transcript import (
    read_task_transcript,
    read_task_transcript_from_disk,
)
from zenagent.app.swarm.workflow import SwarmWorkflow
from zenagent.app.swarm.workitems import WorkItemStore
from zenagent.attachments import read_image_argument
from zenagent.config import load_user_config
from zenagent.mcp.config import parse_server
from zenagent.project import project_key
from zenagent.skills import discover_skills

__all__ = ["APPROVAL_TIMEOUT_S", "SwarmAgent", "SwarmManager"]


@dataclass
class SwarmAgent:
    role: RoleManifest
    runtime: Runtime
    session: Session
    agent: Agent
    #: Which work item this instance is bound to. ``None`` = an ad-hoc run.
    work_item_id: str | None = None
    state: str = "idle"
    current_task: str | None = None
    pending_apply: RoleManifest | None = None

    @property
    def key(self) -> AgentKey:
        return (self.role.id, self.work_item_id)


def _now() -> int:
    return int(time.time())


class SwarmManager(SwarmFrames, SwarmSchedule, SwarmApprovals, SwarmWorkflow):
    def __init__(
        self,
        *,
        root: Path,
        write_frame: Callable[[dict], None] | None = None,
        stream_fn_factory: Callable[[RoleManifest], object] | None = None,
        event_sink: EventSink | None = None,
        max_concurrent: int = 3,
        max_chain_depth: int = DEFAULT_MAX_CHAIN_DEPTH,
        max_chain_cost: float = DEFAULT_MAX_CHAIN_COST,
        runtime_idle_s: float = 600.0,
        supervisor: agents.Supervisor | None = None,
        approval_router=None,
    ) -> None:
        self.root = Path(root)
        self._write_frame = write_frame
        self._stream_fn_factory = stream_fn_factory
        self._event_sink = event_sink
        self._sem = asyncio.Semaphore(max_concurrent)
        self._max_chain_depth = max_chain_depth
        self._max_chain_cost = max_chain_cost
        self.store = SwarmStore(project_state_root(self.root))
        self.work_items = WorkItemStore(self.store)
        self.router: Router | None = None
        self.agents: dict[AgentKey, SwarmAgent] = {}
        self.runners: dict[AgentKey, TaskRunner] = {}
        self.role_ids: set[str] = set()
        self._roles: dict[str, RoleManifest] = {}
        self._idle = IdleTracker(runtime_idle_s)
        self.supervisor = supervisor or agents.Supervisor(self.root)
        self._approval_router = approval_router
        self._vision: dict[str, bool] = {}
        self.warnings: list[str] = []
        self._pending: list[TaskRecord] = []
        self._running: dict[AgentKey, asyncio.Task] = {}
        self._applying: set[AgentKey] = set()
        self._deleting: set[str] = set()
        self._role_locks: dict[str, asyncio.Lock] = {}
        self._wake = asyncio.Event()
        self._loop_task: asyncio.Task | None = None
        self._approvals: dict[str, asyncio.Future] = {}
        self._approval_seq = 0
        # Approval ids carry the project key, so a reply reaches this room.
        self._approval_prefix = f"{project_key(self.root)}:swarm:"
        self._started = False
        self._stopping = False

    @property
    def started(self) -> bool:
        return self._started

    def has_active(self) -> bool:
        """A run in flight (a pending approval counts: its run is awaiting)."""
        return bool(self._running)

    # -- lifecycle ----------------------------------------------------------

    async def start(self) -> None:
        if self._started:
            return
        self._stopping = False
        self.store.load()
        self.warnings.extend(self.store.warnings)
        self.warnings.extend(legacy_room_warnings(has_messages=bool(self.store.messages)))
        reconcile_message = self.store.reconcile()
        if reconcile_message:
            for message in reconcile_message:
                self._emit_message(message)

        self.work_items.load()
        self.warnings.extend(self.work_items.warnings)
        roles, warnings = discover_roles(self.root)
        self.warnings.extend(warnings)
        self._set_roles(roles)
        # Instances are built lazily; membership still comes from configuration.
        self.router = Router(
            self.store,
            self.role_ids,
            work_items=self.work_items,
            max_chain_depth=self._max_chain_depth,
            max_chain_cost=self._max_chain_cost,
        )
        self._started = True
        self._loop_task = asyncio.create_task(self._schedule())

    async def close(self) -> None:
        if not self._started and not self.agents and self._loop_task is None:
            return
        self._stopping = True
        self._wake.set()
        if self._loop_task is not None:
            self._loop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._loop_task
            self._loop_task = None

        for future in self._approvals.values():
            if not future.done():
                future.set_result("deny")

        for key, run in list(self._running.items()):
            runner = self.runners.get(key)
            if runner is not None:
                runner.abort_outcome = ("failed", "interrupted")
            agent = self.agents.get(key)
            if agent is not None:
                agent.agent.abort()
        if self._running:
            await asyncio.gather(*list(self._running.values()), return_exceptions=True)

        for task in list(self._pending):
            current = self.store.tasks.get(task.id)
            if current is not None and not is_terminal(current.state):
                self._append_task(
                    replace(
                        current,
                        state="failed",
                        reason="interrupted",
                        updated_at=_now(),
                    )
                )
        self._pending.clear()

        for key, agent in list(self.agents.items()):
            await agent.runtime.close()
            self.supervisor.unregister(agents.swarm_handle_key(*key))
        self.agents.clear()
        self.runners.clear()
        self._idle = IdleTracker(self._idle.ttl_s)
        self._applying.clear()
        self._approvals.clear()
        self._started = False

    # -- queries ------------------------------------------------------------

    def roles(self) -> list[dict]:
        """The room's roster, read from configuration.

        Not from built instances: a role that has never run has no runtime, and
        the Settings page, the @-completion list and the vision check all need
        to see it anyway.
        """
        views = []
        built = {key[0]: agent for key, agent in self.agents.items()}
        for role_id in sorted(self._roles):
            role = self._roles[role_id]
            agent = built.get(role_id)
            view = role_view(agent.pending_apply or role if agent else role)
            view["supportsVision"] = self._supports_vision(role)
            views.append(view)
        return views

    def role(self, role_id: str) -> RoleManifest:
        return self._roles[role_id]

    def instance(
        self, role_id: str, work_item_id: str | None = None
    ) -> SwarmAgent | None:
        """The run unit for (role, work item); ``None`` before its first round.

        Work item defaults to ``None`` because that is the ad-hoc unit — the
        one every pre-work-item room already has.
        """
        return self.agents.get(agent_key(role_id, work_item_id))

    def _supports_vision(self, role: RoleManifest) -> bool:
        if role.id not in self._vision:
            self._vision[role.id] = supports_vision(role.model)
        return self._vision[role.id]

    def _set_roles(self, roles: list[RoleManifest]) -> None:
        self._roles = {role.id: role for role in roles}
        self.role_ids = set(self._roles)
        self._vision = {}
        if self.router is not None:
            self.router.role_ids = set(self.role_ids)

    def agents_state(self) -> list[dict]:
        return [
            {
                "agent": agent.role.id,
                "name": agent.role.name,
                "workItem": agent.work_item_id,
                "state": agent.state,
                "task": agent.current_task,
                "pendingApply": agent.pending_apply is not None,
            }
            for agent in self.agents.values()
        ]

    def tasks(self) -> list[dict]:
        records = sorted(
            self.store.tasks.values(), key=lambda task: (task.created_at, task.id)
        )
        return [self.task_view(task) for task in records]

    def task_view(self, task: TaskRecord) -> dict:
        record = _task_record(task)
        record.pop("type", None)
        record.pop("version", None)
        return record

    def messages(self, since: int | None = None) -> list[dict]:
        start = 0 if since is None else max(0, int(since))
        payload = []
        for message in self.store.messages[start:]:
            record = _message_record(message)
            record.pop("type", None)
            record.pop("version", None)
            payload.append(record)
        return payload

    def task_transcript(self, task_id: str) -> dict:
        """The steps of one run, read from its assignee's session.

        A task survives its role (the room log is older), so a missing agent
        or a reset session reports ``available: False`` instead of failing:
        the detail card then says the record is gone rather than erroring.
        """
        task = self.store.tasks.get(task_id)
        if task is None:
            return {"available": False, "transcript": []}
        agent = self.agents.get((task.assignee, task.work_item_id))
        if agent is not None:
            available, rows = read_task_transcript(
                agent.session.path, task_id, running=task.state == "running"
            )
            return {"available": available, "transcript": rows}
        rows = read_task_transcript_from_disk(
            sessions_dir(
                task.assignee, root=self.root, work_item_id=task.work_item_id
            ),
            task_id,
        )
        if rows is None:
            return {"available": False, "transcript": []}
        return {"available": True, "transcript": rows}

    # -- room commands ------------------------------------------------------

    async def post(
        self,
        text: str,
        *,
        ref: str | None = None,
        images: list[str] | None = None,
    ) -> list[TaskRecord]:
        mentions = parse_mentions(text)
        quote = self._quote_snapshot(ref)
        paths = self._image_paths(images or [], mentions)
        message = RoomMessage(
            id=self.store.new_id("m"),
            ts=_now(),
            author_kind="user",
            author_id="you",
            text=text,
            mentions=mentions,
            task_id=None,
            chain_depth=0,
            ref=quote,
            images=paths,
        )
        self.store.append_message(message)
        self._emit_message(message)
        work_item_id = self._resolve_work_item(message, ref)
        return self._admit(message, work_item_id=work_item_id)

    def _quote_snapshot(self, ref: str | None) -> RoomRef | None:
        if ref is None:
            return None
        source = next((item for item in self.store.messages if item.id == ref), None)
        if source is None:
            raise ValueError(f"unknown ref {ref}")
        if source.author_kind == "system":
            raise ValueError(f"cannot quote a system message {ref}")
        return RoomRef(
            id=source.id,
            author_kind=source.author_kind,
            author_id=source.author_id,
            text=source.text,
        )

    def _image_paths(self, images: list[str], mentions: list[str]) -> list[str]:
        if not images:
            return []
        for path in images:
            read_image_argument(Path(path))
        for role_id in mentions:
            role = self._roles.get(role_id)
            if role is not None and not self._supports_vision(role):
                raise ValueError(f"@{role_id} cannot accept images")
        return list(images)

    async def cancel(self, task_id: str) -> bool:
        task = self.store.tasks.get(task_id)
        if task is None or is_terminal(task.state):
            return False
        if task.state == "queued":
            self._pending = [item for item in self._pending if item.id != task_id]
            self._append_task(
                replace(task, state="cancelled", reason="aborted", updated_at=_now())
            )
            return True
        key = (task.assignee, task.work_item_id)
        agent = self.agents.get(key)
        if agent is None:
            return False
        runner = self.runners.get(key)
        if runner is not None:
            runner.abort_outcome = ("cancelled", "aborted")
        agent.agent.abort()
        return True

    async def retry(self, task_id: str) -> TaskRecord | None:
        task = self.store.tasks.get(task_id)
        if task is None or not is_terminal(task.state):
            return None
        if task.source_message_id is None:
            return None
        now = _now()
        new_task = TaskRecord(
            id=self.store.new_id("t"),
            assignee=task.assignee,
            state="queued",
            source_message_id=task.source_message_id,
            created_by_kind=task.created_by_kind,
            created_by_id=task.created_by_id,
            chain_id=task.chain_id,
            chain_depth=task.chain_depth,
            chain_cost=task.chain_cost,
            created_at=now,
            updated_at=now,
            derived_from=task.id,
            work_item_id=task.work_item_id,
        )
        self._append_task(new_task)
        self._pending.append(new_task)
        self._wake.set()
        return new_task

    async def reset(self, agent_id: str, work_item_id: str | None = None) -> bool:
        """Start a fresh session for one (agent, work item).

        Scoped on purpose: resetting one job must not wipe the context of
        another job the same agent is running.
        """
        key = agent_key(agent_id, work_item_id)
        agent = self.agents.get(key)
        if agent is None or agent.state == "running":
            return False
        self._applying.add(key)
        try:
            await self._rebuild_agent(key, role=agent.role, fresh_session=True)
        finally:
            self._applying.discard(key)
            self._wake.set()
        self._system_note(f"Session reset for @{agent_id}")
        return True

    # -- role lifecycle -----------------------------------------------------

    def _role_lock(self, role_id: str) -> asyncio.Lock:
        return self._role_locks.setdefault(role_id, asyncio.Lock())

    async def update_role(self, role_id: str, manifest: dict) -> RoleManifest:
        role = write_role(role_id, manifest, root=self.root)
        self._roles[role_id] = role
        self.role_ids.add(role_id)
        self._vision.pop(role_id, None)
        if self.router is not None:
            self.router.role_ids = set(self.role_ids)
        await self._apply_role(role)
        return role

    def _cancel_pending(self, role_id: str) -> None:
        for task in list(self._pending):
            if task.assignee != role_id:
                continue
            self._pending = [item for item in self._pending if item.id != task.id]
            current = self.store.tasks.get(task.id)
            if current is not None and not is_terminal(current.state):
                self._append_task(
                    replace(
                        current,
                        state="cancelled",
                        reason="role_removed",
                        updated_at=_now(),
                    )
                )

    async def delete_role(self, role_id: str) -> None:
        self._deleting.add(role_id)
        try:
            self._cancel_pending(role_id)
            async with self._role_lock(role_id):
                keys = [key for key in self.agents if key[0] == role_id]
                runs = [
                    (key, self._running[key])
                    for key in keys
                    if key in self._running
                ]
                for key, _run in runs:
                    runner = self.runners.get(key)
                    if runner is not None:
                        runner.abort_outcome = ("cancelled", "role_removed")
                    agent = self.agents.get(key)
                    if agent is not None:
                        agent.agent.abort()
                if runs:
                    await asyncio.gather(
                        *[run for _key, run in runs], return_exceptions=True
                    )
                # Close the CURRENT runtimes, re-read after the runs settled: a
                # rebuild that landed mid-cancel must not be orphaned by popping
                # a new agent while closing an old runtime.
                for key in [key for key in self.agents if key[0] == role_id]:
                    agent = self.agents.pop(key)
                    self.runners.pop(key, None)
                    self.supervisor.unregister(agents.swarm_handle_key(*key))
                    self._idle.forget(key)
                    await agent.runtime.close()
        finally:
            self._deleting.discard(role_id)
            self._wake.set()
        self._roles.pop(role_id, None)
        self._vision.pop(role_id, None)
        self.role_ids.discard(role_id)
        if self.router is not None:
            self.router.role_ids = set(self.role_ids)
        self._cancel_pending(role_id)
        remove_role_dir(role_id, root=self.root)

    async def reload(self) -> list[str]:
        roles, warnings = discover_roles(self.root)
        self.warnings = list(self.store.warnings)
        self.warnings.extend(warnings)
        for agent in self.agents.values():
            self.warnings.extend(agent.runtime.warnings)
        notes = list(warnings)
        incoming = {role.id: role for role in roles}
        for role_id in self._roles.keys() - incoming.keys():
            await self.delete_role(role_id)
            notes.append(f"removed {role_id}")
        for role_id, role in incoming.items():
            existing = self._roles.get(role_id)
            if existing is None:
                notes.append(f"added {role_id}")
            elif role_signature(existing) != role_signature(role):
                notes.append(f"updated {role_id}")
                await self._apply_role(role)
        self._set_roles(roles)
        return notes

    # -- runtime assembly ---------------------------------------------------

    async def _try_build_agent(
        self,
        role: RoleManifest,
        *,
        work_item_id: str | None = None,
        fresh_session: bool = False,
    ) -> SwarmAgent | None:
        try:
            return await self._build_agent(
                role, work_item_id=work_item_id, fresh_session=fresh_session
            )
        except Exception as exc:  # noqa: BLE001 - one broken role must not kill the swarm
            detail = f"{type(exc).__name__}: {exc}"
            self.warnings.append(f"role {role.id}: {detail}")
            key = agent_key(role.id, work_item_id)
            self.agents.pop(key, None)
            self.runners.pop(key, None)
            self.supervisor.unregister(agents.swarm_handle_key(role.id, work_item_id))
            self._system_note(f"Role @{role.id} is unavailable: {detail}")
            return None

    async def _instance(
        self, role: RoleManifest, work_item_id: str | None
    ) -> SwarmAgent | None:
        """The instance for one (role, work item), built on first use.

        Laziness keeps "same agent, two work items" affordable.
        """
        key = agent_key(role.id, work_item_id)
        agent = self.agents.get(key)
        if agent is None:
            agent = await self._try_build_agent(role, work_item_id=work_item_id)
            if agent is None:
                return None
        self._idle.touch(key)
        return agent

    async def _build_agent(
        self,
        role: RoleManifest,
        *,
        work_item_id: str | None = None,
        fresh_session: bool = False,
    ) -> SwarmAgent:
        session_path = role_session_path(
            role.id, root=self.root, work_item_id=work_item_id, fresh=fresh_session
        )
        config = load_user_config(cwd=role.root)
        key = agent_key(role.id, work_item_id)
        profile = agents.AgentProfile(
            id=role.id,
            kind="swarm",
            label=role.name,
            tools_allow=None if role.tools is None else tuple(role.tools),
            extra_tools=(
                build_swarm_post_tool(
                    lambda text, key=key: self._post_from(key, text)
                ),
                *self.work_tools(opened_by=role.id, opened_from=work_item_id),
            ),
            extra_prompt=f"{role.system_prompt}\n\n{PROTOCOL_TEXT}",
            plugins=False,
            skills=False,
            templates=False,
            mcp=False,
            context_files=role.inherit_project,
            project_sessions=False,
        )
        options = RuntimeOptions(
            root=role.root,
            model_id=role.model,
            thinking=role.thinking,
            mode=role.mode,
            max_turns=role.max_turns,
            max_cost=role.max_cost,
            profile=profile,
            resume=session_path,
        )
        stream_fn = (
            self._stream_fn_factory(role)
            if self._stream_fn_factory is not None
            else None
        )
        # Same assembly path as every frontend: one place wires the broker,
        # the run recorder and the rest, so a new step there reaches swarm too.
        prepared = await prepare(
            options,
            stream_fn=stream_fn,
            allow_missing_key=True,
            views=False,
        )
        broker = prepared.broker
        broker.set_confirm(self._confirm_callback(key))
        runtime = prepared.runtime
        attach_swarm_run(runtime, role.id, work_item_id, role.name)
        agents.register_swarm_agent(self.supervisor, profile, runtime, work_item_id)
        try:
            self.warnings.extend(runtime.warnings)
            await self._attach_role_tools(
                runtime, role, retry_interval_s=options.mcp_retry_interval_s
            )
            runtime.ask_handler = self._ask_handler(role.id)
            runtime._recompose()
        except Exception:
            await runtime.close()
            raise
        if self._event_sink is not None:
            # Run events carry the task in flight (the file only lands at run end).
            subscribe_events(
                runtime.agent,
                role.id,
                sink=self._event_sink,
                task_of=lambda _role_id, key=key: getattr(
                    self.agents.get(key), "current_task", None
                ),
                work_item_of=lambda _role_id, work_item_id=work_item_id: work_item_id,
            )

        runner = TaskRunner(
            record_task=self._append_task,
            agent=runtime.agent,
            session=runtime.session,
            role=role,
            post_message=lambda text, kind, key=key: self._post_from(
                key, text, kind, admit=False
            ),
            admit_message=self._admit,
            run_timeout_s=config.task_timeout_s,
        )
        agent = SwarmAgent(
            role=role,
            runtime=runtime,
            session=runtime.session,
            agent=runtime.agent,
            work_item_id=work_item_id,
        )
        self.agents[key] = agent
        self.runners[key] = runner
        self._emit_agent(key)
        return agent

    async def _attach_role_tools(
        self,
        runtime: Runtime,
        role: RoleManifest,
        *,
        retry_interval_s: float,
    ) -> None:
        if role.skill_roots:
            skills, warnings = discover_skills(role.skill_roots)
            runtime.warnings.extend(warnings)
            runtime.skills = skills
        configs = {}
        for name, entry in role.mcp_servers.items():
            try:
                configs[name] = parse_server(name, entry)
            except ValueError as exc:  # pragma: no cover - parse_role validates
                runtime.warnings.append(f"mcp {name}: {exc}")
        if configs:
            runtime.mcp = await start_mcp(
                configs,
                retry_interval_s=retry_interval_s,
                runtime=runtime,
            )

    async def _rebuild_agent(
        self,
        key: AgentKey,
        *,
        role: RoleManifest,
        fresh_session: bool = False,
    ) -> SwarmAgent | None:
        old = self.agents.pop(key, None)
        self.runners.pop(key, None)
        self.supervisor.unregister(agents.swarm_handle_key(*key))
        if old is not None:
            await old.runtime.close()
        return await self._try_build_agent(
            role, work_item_id=key[1], fresh_session=fresh_session
        )

    async def _apply_role(self, role: RoleManifest) -> None:
        """Apply an edited manifest to every instance of that role.

        A role's config is shared by all its work items, so a rebuild that only
        touched one of them would leave two versions of the same agent running.
        A busy instance defers to ``pending_apply`` (its run owns the runtime).
        """
        async with self._role_lock(role.id):
            if role.id in self._deleting:
                return
            keys = [key for key in self.agents if key[0] == role.id]
            pending_keys = []
            for key in keys:
                agent = self.agents[key]
                agent.role = role
                if agent.state == "running":
                    agent.pending_apply = role
                    pending_keys.append(key)
                    self._emit_agent(key)
            rebuild = [key for key in keys if key not in pending_keys]
            if not rebuild:
                return
            self._applying.update(rebuild)
            try:
                for key in rebuild:
                    await self._rebuild_agent(key, role=role)
            finally:
                self._applying.difference_update(rebuild)
                self._wake.set()

    # -- posting ------------------------------------------------------------

    def _admit(
        self, message: RoomMessage, *, work_item_id: str | None = None
    ) -> list[TaskRecord]:
        assert self.router is not None
        if message.author_kind == "agent" and "you" in message.mentions:
            self._write({
                "type": "swarm_notice", "swarm": True, "agent": message.author_id,
                "text": f"@{message.author_id}: {message.text[:280]}",
            })
        admission = self.router.admit(
            message, inbox_sizes=self._inbox_sizes(), work_item_id=work_item_id
        )
        for role_id, reason in admission.rejected:
            self._system_note(f"Delegation to @{role_id} rejected: {reason}")
        self._pending.extend(admission.accepted)
        for task in admission.accepted:
            self._emit_task(task)
        if admission.accepted:
            self._wake.set()
        return admission.accepted

    def _inbox_sizes(self) -> dict[AgentKey, int]:
        """Queued rounds per run unit, so one busy job cannot flood another."""
        sizes: dict[AgentKey, int] = {}
        for task in self._pending:
            key = agent_key(task.assignee, task.work_item_id)
            sizes[key] = sizes.get(key, 0) + 1
        return sizes

    def _post_from(
        self, key: AgentKey, text: str, kind: str = "agent", *, admit: bool = True
    ) -> RoomMessage:
        """Post on behalf of one run unit.

        The instance already knows its work item, so a delegation inherits it
        directly: an agent @-mentioning a peer is asking for help with *this*
        job, not opening a new one.
        """
        agent = self.agents.get(key)
        current = agent.current_task if agent is not None else None
        source = self.store.tasks.get(current) if current else None
        message = RoomMessage(
            id=self.store.new_id("m"),
            ts=_now(),
            author_kind=kind,
            author_id=key[0] if kind == "agent" else "system",
            text=text,
            mentions=parse_mentions(text) if kind == "agent" else [],
            task_id=current,
            chain_depth=source.chain_depth if source is not None else 0,
        )
        self.store.append_message(message)
        self._emit_message(message)
        if kind == "agent" and admit:
            self._admit(message, work_item_id=key[1])
        return message

    def _system_note(self, text: str) -> RoomMessage:
        message = RoomMessage(
            id=self.store.new_id("m"),
            ts=_now(),
            author_kind="system",
            author_id="system",
            text=text,
        )
        self.store.append_message(message)
        self._emit_message(message)
        return message
