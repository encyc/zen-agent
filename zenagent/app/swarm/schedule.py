"""Scheduling and per-round execution for the swarm manager.

Extracted from ``SwarmManager`` to keep that file under its architecture LOC
budget. A mixin, like :mod:`zenagent.app.swarm.frames`: it declares the state it
needs and owns none of it.

The scheduling unit is ``(role, work item)``: one agent may be working two work
items at once, each with its own runtime and session, so "busy" is per pair
rather than per role.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import replace

from zenagent.app.run_wiring import update_run_scope
from zenagent.app.swarm.instances import AgentKey, IdleTracker
from zenagent.app.swarm.room import TaskRecord, is_terminal

__all__ = ["SwarmSchedule"]


def _now() -> int:
    return int(time.time())


class SwarmSchedule:
    """Submit, run and settle rounds; mixed into SwarmManager."""

    _pending: list[TaskRecord]
    _running: dict[AgentKey, asyncio.Task]
    _applying: set[AgentKey]
    _deleting: set[str]
    _stopping: bool
    _idle: IdleTracker

    # -- scheduling ---------------------------------------------------------

    def _next_entry(self):
        """The first queued round whose run unit is idle.

        The instance may not exist yet — it is built lazily — so absence is not
        a reason to skip: the role comes from configuration.
        """
        busy = set(self._running) | set(self._applying)
        for task in self._pending:
            key = (task.assignee, task.work_item_id)
            if key in busy or task.assignee in self._deleting:
                continue
            if task.assignee not in self.role_ids:
                continue
            return task, key
        return None

    async def _schedule(self) -> None:
        while not self._stopping:
            entry = self._next_entry()
            if entry is None:
                # Clear before re-checking so a wake that landed while we were
                # scheduling is not lost; then reap and sleep with a timeout so
                # an idle instance cannot outlive its TTL.
                self._wake.clear()
                if self._next_entry() is not None:
                    continue
                await self._reap_idle()
                if self._next_entry() is not None:
                    continue
                await self._wait_for_work()
                continue
            task, key = entry
            agent = await self._instance(self._roles[task.assignee], key[1])
            if agent is None:
                # The role failed to build (bad manifest, missing credentials):
                # fail this round instead of spinning on it forever.
                self._pending = [item for item in self._pending if item.id != task.id]
                self._fail_task(task, "role_unavailable")
                continue
            await self._sem.acquire()
            current = self.store.tasks.get(task.id)
            if self._stopping or current is None or current.state != "queued":
                self._sem.release()
                self._pending = [item for item in self._pending if item.id != task.id]
                continue
            self._pending = [item for item in self._pending if item.id != task.id]
            run = asyncio.create_task(self._execute(task, agent))
            self._running[key] = run

    @property
    def _reap_interval(self) -> float:
        ttl = self._idle.ttl_s
        if ttl <= 0:
            return 60.0
        return max(0.05, min(60.0, ttl / 2))

    async def _wait_for_work(self) -> None:
        if self._idle.ttl_s <= 0:
            await self._wake.wait()
            return
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=self._reap_interval)
        except TimeoutError:
            pass

    def _has_pending(self, key: AgentKey) -> bool:
        return any((task.assignee, task.work_item_id) == key for task in self._pending)

    async def _reap_idle(self) -> None:
        """Close runtimes that have been idle longer than the TTL.

        A round in flight, a queued round, a pending manifest apply and a role
        being deleted all pin the instance. The session file is the fact
        source, so a reaped instance is rebuilt by ``_instance`` on the next
        round.
        """
        if self._idle.ttl_s <= 0 or self._stopping:
            return
        for key in list(self._idle.keys()):
            if key in self._running or key in self._applying:
                continue
            if key[0] in self._deleting or self._has_pending(key):
                continue
            agent = self.agents.get(key)
            if agent is None or agent.pending_apply is not None:
                continue
            if not self._idle.idle(key):
                continue
            await self._reap_one(key)

    async def _reap_one(self, key: AgentKey) -> None:
        async with self._role_lock(key[0]):
            if self._stopping or key in self._running or key in self._applying:
                return
            agent = self.agents.get(key)
            if (
                agent is None
                or agent.pending_apply is not None
                or self._has_pending(key)
            ):
                return
            self.agents.pop(key, None)
            self.runners.pop(key, None)
            self._idle.forget(key)
            with contextlib.suppress(Exception):
                await agent.runtime.close()

    async def _execute(self, task: TaskRecord, agent) -> None:
        key: AgentKey = (agent.role.id, agent.work_item_id)
        role_id = agent.role.id
        message = next(
            (item for item in self.store.messages if item.id == task.source_message_id),
            None,
        )
        runner = self.runners.get(key)
        try:
            if message is None or runner is None:
                self._fail_task(task, "missing_source")
                return
            agent.state = "running"
            agent.current_task = task.id
            update_run_scope(agent.runtime, taskId=task.id)
            self._idle.touch(key)
            self._emit_agent(key)
            await runner.run(task, message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - one run must not kill the manager
            self._fail_task(task, "error", detail=f"{type(exc).__name__}: {exc}")
        finally:
            agent.state = "idle"
            agent.current_task = None
            try:
                # delete_role owns this role now: it closes+pops under the lock,
                # so don't rebuild a doomed agent — and don't deadlock on the
                # lock delete holds while awaiting this very run.
                if role_id not in self._deleting:
                    async with self._role_lock(role_id):
                        pending = agent.pending_apply
                        agent.pending_apply = None
                        if pending is not None and not self._stopping:
                            agent.state = "rebuilding"
                            self._emit_agent(key)
                            await self._rebuild_agent(key, role=pending)
                        elif self.agents.get(key) is agent:
                            self._emit_agent(key)
            finally:
                self._running.pop(key, None)
                self._idle.touch(key)
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
