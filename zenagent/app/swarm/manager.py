"""SwarmManager: one runtime per role, a FIFO scheduler and room lifecycle.

Each role gets its own ``Runtime`` (isolated workspace, own agent session) and
one at a time runs a task from its inbox. The manager owns everything the
runner deliberately does not: inbox sizes for the router gates, the approval
broker in front of each runtime, task scheduling, and the role lifecycle
(update while running, delete, reset, reload).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from zenagent.agent.agent import Agent
from zenagent.agent.session import Session
from zenagent.app.permissions import build_broker_for
from zenagent.app.plugin_wiring import start_mcp
from zenagent.app.runtime import Runtime, RuntimeOptions
from zenagent.app.swarm.protocol import (
    PROTOCOL_TEXT,
    build_swarm_post_tool,
)
from zenagent.app.swarm.roles import (
    RoleManifest,
    discover_roles,
    state_root,
)
from zenagent.app.swarm.room import (
    RoomMessage,
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
from zenagent.app.swarm.router import Router
from zenagent.app.swarm.runner import TaskRunner
from zenagent.config import load_user_config
from zenagent.mcp.config import parse_server
from zenagent.skills import discover_skills

__all__ = ["APPROVAL_TIMEOUT_S", "SwarmAgent", "SwarmManager"]

APPROVAL_TIMEOUT_S = 120.0


@dataclass
class SwarmAgent:
    role: RoleManifest
    runtime: Runtime
    session: Session
    agent: Agent
    state: str = "idle"
    current_task: str | None = None
    pending_apply: RoleManifest | None = None


def _now() -> int:
    return int(time.time())


class SwarmManager:
    def __init__(
        self,
        *,
        write_frame: Callable[[dict], None] | None = None,
        stream_fn_factory: Callable[[RoleManifest], object] | None = None,
        max_concurrent: int = 3,
    ) -> None:
        self._write_frame = write_frame
        self._stream_fn_factory = stream_fn_factory
        self._sem = asyncio.Semaphore(max_concurrent)
        self.store = SwarmStore(state_root())
        self.router: Router | None = None
        self.agents: dict[str, SwarmAgent] = {}
        self.runners: dict[str, TaskRunner] = {}
        self.role_ids: set[str] = set()
        self.warnings: list[str] = []
        self._pending: list[TaskRecord] = []
        self._running: dict[str, asyncio.Task] = {}
        self._applying: set[str] = set()
        self._wake = asyncio.Event()
        self._loop_task: asyncio.Task | None = None
        self._approvals: dict[str, asyncio.Future] = {}
        self._approval_seq = 0
        self._started = False
        self._stopping = False

    # -- lifecycle ----------------------------------------------------------

    async def start(self) -> None:
        if self._started:
            return
        self._stopping = False
        self.store.load()
        self.warnings.extend(self.store.warnings)
        reconcile_message = self.store.reconcile()
        if reconcile_message:
            for message in reconcile_message:
                self._emit_message(message)

        roles, warnings = discover_roles()
        self.warnings.extend(warnings)
        self.role_ids = {role.id for role in roles}
        self.router = Router(self.store, self.role_ids)
        for role in roles:
            await self._build_agent(role)
        self._started = True
        self._loop_task = asyncio.create_task(self._schedule())

    async def close(self) -> None:
        if not self._started:
            return
        self._stopping = True
        self._wake.set()
        if self._loop_task is not None:
            self._loop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._loop_task
            self._loop_task = None

        for role_id, run in list(self._running.items()):
            runner = self.runners.get(role_id)
            if runner is not None:
                runner.abort_outcome = ("failed", "interrupted")
            agent = self.agents.get(role_id)
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

        for agent in list(self.agents.values()):
            await agent.runtime.close()
        self.agents.clear()
        self.runners.clear()
        self._applying.clear()
        self._approvals.clear()
        self._started = False

    # -- queries ------------------------------------------------------------

    def roles(self) -> list[dict]:
        return [
            role_view(agent.pending_apply or agent.role)
            for agent in self.agents.values()
        ]

    def agents_state(self) -> list[dict]:
        return [
            {
                "agent": agent.role.id,
                "name": agent.role.name,
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

    # -- room commands ------------------------------------------------------

    async def post(self, text: str) -> list[TaskRecord]:
        message = RoomMessage(
            id=self.store.new_id("m"),
            ts=_now(),
            author_kind="user",
            author_id="you",
            text=text,
            mentions=parse_mentions(text),
            task_id=None,
            chain_depth=0,
        )
        self.store.append_message(message)
        self._emit_message(message)
        return self._admit(message)

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
        agent = self.agents.get(task.assignee)
        if agent is None:
            return False
        runner = self.runners.get(task.assignee)
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
        )
        self._append_task(new_task)
        self._pending.append(new_task)
        self._wake.set()
        return new_task

    async def reset(self, agent_id: str) -> bool:
        agent = self.agents.get(agent_id)
        if agent is None or agent.state == "running":
            return False
        self._applying.add(agent_id)
        try:
            await self._rebuild_agent(agent_id, role=agent.role, fresh_session=True)
        finally:
            self._applying.discard(agent_id)
            self._wake.set()
        self._system_note(f"Session reset for @{agent_id}")
        return True

    # -- role lifecycle -----------------------------------------------------

    async def update_role(self, role_id: str, manifest: dict) -> RoleManifest:
        role = write_role(role_id, manifest)
        self.role_ids.add(role_id)
        if self.router is not None:
            self.router.role_ids = set(self.role_ids)
        await self._apply_role(role)
        return role

    async def delete_role(self, role_id: str) -> None:
        agent = self.agents.get(role_id)
        if agent is not None:
            self._applying.add(role_id)
            try:
                for task in list(self._pending):
                    if task.assignee != role_id:
                        continue
                    self._pending = [
                        item for item in self._pending if item.id != task.id
                    ]
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
                run = self._running.get(role_id)
                if run is not None:
                    runner = self.runners.get(role_id)
                    if runner is not None:
                        runner.abort_outcome = ("cancelled", "role_removed")
                    agent.agent.abort()
                    await asyncio.gather(run, return_exceptions=True)
                await agent.runtime.close()
                self.agents.pop(role_id, None)
                self.runners.pop(role_id, None)
            finally:
                self._applying.discard(role_id)
                self._wake.set()
        self.role_ids.discard(role_id)
        if self.router is not None:
            self.router.role_ids = set(self.role_ids)
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
        remove_role_dir(role_id)

    async def reload(self) -> list[str]:
        roles, warnings = discover_roles()
        self.warnings.extend(warnings)
        notes = list(warnings)
        incoming = {role.id: role for role in roles}
        self.role_ids = set(incoming)
        if self.router is not None:
            self.router.role_ids = set(self.role_ids)
        for role_id in set(self.agents) - set(incoming):
            await self.delete_role(role_id)
            notes.append(f"removed {role_id}")
        for role_id, role in incoming.items():
            existing = self.agents.get(role_id)
            if existing is None:
                await self._build_agent(role)
                notes.append(f"added {role_id}")
            elif role_signature(existing.role) != role_signature(role):
                await self._apply_role(role)
                notes.append(f"updated {role_id}")
        return notes

    # -- approvals ----------------------------------------------------------

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

    # -- runtime assembly ---------------------------------------------------

    async def _build_agent(
        self, role: RoleManifest, *, fresh_session: bool = False
    ) -> SwarmAgent:
        session_path = self._session_path(role.id, fresh=fresh_session)
        broker = build_broker_for(
            user_config=load_user_config(cwd=role.root),
            mode=role.mode,
            cwd=role.root,
        )
        broker.set_confirm(self._confirm_callback(role.id))
        options = RuntimeOptions(
            root=role.root,
            model_id=role.model,
            thinking=role.thinking,
            mode=role.mode,
            max_turns=role.max_turns,
            max_cost=role.max_cost,
            mcp=False,
            skills=False,
            templates=False,
            context_files=role.inherit_project,
            tools_allow=role.tools,
            extra_tools=[
                build_swarm_post_tool(
                    lambda text, role_id=role.id: self._post_from(role_id, text)
                )
            ],
            extra_prompt=f"{role.system_prompt}\n\n{PROTOCOL_TEXT}",
            resume=session_path,
        )
        stream_fn = (
            self._stream_fn_factory(role)
            if self._stream_fn_factory is not None
            else None
        )
        runtime = await Runtime.bootstrap(
            options,
            stream_fn=stream_fn,
            gate=broker,
            allow_missing_key=True,
        )
        self.warnings.extend(runtime.warnings)
        await self._attach_role_tools(
            runtime, role, retry_interval_s=options.mcp_retry_interval_s
        )
        runtime.ask_handler = self._ask_handler(role.id)
        runtime._recompose()

        runner = TaskRunner(
            store=self.store,
            agent=runtime.agent,
            session=runtime.session,
            role=role,
            post_message=lambda text, kind, role_id=role.id: self._post_from(
                role_id, text, kind
            ),
        )
        agent = SwarmAgent(
            role=role,
            runtime=runtime,
            session=runtime.session,
            agent=runtime.agent,
        )
        self.agents[role.id] = agent
        self.runners[role.id] = runner
        self._emit_agent(role.id)
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
        role_id: str,
        *,
        role: RoleManifest,
        fresh_session: bool = False,
    ) -> SwarmAgent:
        old = self.agents.pop(role_id, None)
        self.runners.pop(role_id, None)
        if old is not None:
            await old.runtime.close()
        return await self._build_agent(role, fresh_session=fresh_session)

    async def _apply_role(self, role: RoleManifest) -> None:
        agent = self.agents.get(role.id)
        if agent is None:
            await self._build_agent(role)
            return
        if agent.state == "running":
            agent.pending_apply = role
            self._emit_agent(role.id)
            return
        self._applying.add(role.id)
        try:
            await self._rebuild_agent(role.id, role=role)
        finally:
            self._applying.discard(role.id)
            self._wake.set()

    # -- scheduling ---------------------------------------------------------

    def _next_entry(self) -> tuple[TaskRecord, SwarmAgent] | None:
        busy = set(self._running) | set(self._applying)
        for task in self._pending:
            if task.assignee in busy:
                continue
            agent = self.agents.get(task.assignee)
            if agent is None:
                continue
            return task, agent
        return None

    async def _schedule(self) -> None:
        while not self._stopping:
            entry = self._next_entry()
            if entry is None:
                self._wake.clear()
                await self._wake.wait()
                continue
            task, agent = entry
            await self._sem.acquire()
            current = self.store.tasks.get(task.id)
            if self._stopping or current is None or current.state != "queued":
                self._sem.release()
                self._pending = [item for item in self._pending if item.id != task.id]
                continue
            self._pending = [item for item in self._pending if item.id != task.id]
            run = asyncio.create_task(self._execute(task, agent))
            self._running[agent.role.id] = run

    async def _execute(self, task: TaskRecord, agent: SwarmAgent) -> None:
        role_id = agent.role.id
        message = next(
            (item for item in self.store.messages if item.id == task.source_message_id),
            None,
        )
        runner = self.runners.get(role_id)
        try:
            if message is None or runner is None:
                self._fail_task(task, "missing_source")
                return
            agent.state = "running"
            agent.current_task = task.id
            self._emit_agent(role_id)
            self._emit_task(replace(task, state="running", updated_at=_now()))
            await runner.run(task, message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - one run must not kill the manager
            self._fail_task(task, "error", detail=f"{type(exc).__name__}: {exc}")
        finally:
            agent.state = "idle"
            agent.current_task = None
            pending = agent.pending_apply
            agent.pending_apply = None
            try:
                if pending is not None and not self._stopping:
                    agent.state = "rebuilding"
                    self._emit_agent(role_id)
                    await self._rebuild_agent(role_id, role=pending)
                elif self.agents.get(role_id) is agent:
                    self._emit_agent(role_id)
            finally:
                self._running.pop(role_id, None)
                self._sem.release()
                self._wake.set()

    def _fail_task(
        self, task: TaskRecord, reason: str, *, detail: str | None = None
    ) -> None:
        current = self.store.tasks.get(task.id)
        if current is not None and not is_terminal(current.state):
            self._append_task(
                replace(current, state="failed", reason=reason, updated_at=_now())
            )
        text = f"Task {task.id} failed: {reason}"
        if detail:
            text = f"{text} ({detail})"
        self._system_note(text)

    # -- posting ------------------------------------------------------------

    def _admit(self, message: RoomMessage) -> list[TaskRecord]:
        assert self.router is not None
        admission = self.router.admit(message, inbox_sizes=self._inbox_sizes())
        for role_id, reason in admission.rejected:
            self._system_note(f"Delegation to @{role_id} rejected: {reason}")
        self._pending.extend(admission.accepted)
        for task in admission.accepted:
            self._emit_task(task)
        if admission.accepted:
            self._wake.set()
        return admission.accepted

    def _inbox_sizes(self) -> dict[str, int]:
        sizes: dict[str, int] = {}
        for task in self._pending:
            sizes[task.assignee] = sizes.get(task.assignee, 0) + 1
        return sizes

    def _post_from(self, role_id: str, text: str, kind: str = "agent") -> RoomMessage:
        agent = self.agents.get(role_id)
        current = agent.current_task if agent is not None else None
        source = self.store.tasks.get(current) if current else None
        message = RoomMessage(
            id=self.store.new_id("m"),
            ts=_now(),
            author_kind=kind,
            author_id=role_id if kind == "agent" else "system",
            text=text,
            mentions=parse_mentions(text) if kind == "agent" else [],
            task_id=current,
            chain_depth=source.chain_depth if source is not None else 0,
        )
        self.store.append_message(message)
        self._emit_message(message)
        if kind == "agent":
            self._admit(message)
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

    # -- agent sessions -----------------------------------------------------

    def _session_path(self, role_id: str, *, fresh: bool = False) -> Path:
        directory = state_root() / "agents" / role_id / "sessions"
        directory.mkdir(parents=True, exist_ok=True)
        if not fresh:
            latest = _latest_session(directory)
            if latest is not None:
                return latest
        stamp = time.strftime("%Y%m%dT%H%M%S", time.localtime())
        path = directory / f"{stamp}-{time.time_ns() % 1_000_000_000}.jsonl"
        path.touch()
        return path

    # -- broker callbacks ---------------------------------------------------

    def _confirm_callback(self, role_id: str):
        async def confirm(tool_name: str, arguments: dict, verdict=None) -> str:
            agent = self.agents.get(role_id)
            task_id = agent.current_task if agent is not None else None
            request_id = f"swarm:a{self._approval_seq}"
            self._approval_seq += 1
            event: dict = {
                "type": "approval_request",
                "swarm": True,
                "agent": role_id,
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
                runner = self.runners.get(role_id)
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

    # -- frames -------------------------------------------------------------

    def _write(self, payload: dict) -> None:
        if self._write_frame is not None:
            self._write_frame(payload)

    def _emit_message(self, message: RoomMessage) -> None:
        record = _message_record(message)
        record.update(type="swarm_message", swarm=True)
        self._write(record)

    def _emit_task(self, task: TaskRecord) -> None:
        record = _task_record(task)
        record.update(type="swarm_task", swarm=True)
        self._write(record)

    def _emit_agent(self, role_id: str) -> None:
        agent = self.agents.get(role_id)
        if agent is None:
            return
        self._write(
            {
                "type": "swarm_agent",
                "swarm": True,
                "agent": role_id,
                "name": agent.role.name,
                "state": agent.state,
                "task": agent.current_task,
                "pendingApply": agent.pending_apply is not None,
            }
        )

    def _append_task(self, task: TaskRecord) -> None:
        self.store.append_task(task)
        self._emit_task(task)


def _latest_session(directory: Path) -> Path | None:
    files = []
    for path in directory.glob("*.jsonl"):
        try:
            files.append((path.stat().st_mtime, path))
        except OSError:
            continue
    if not files:
        return None
    return max(files)[1]


