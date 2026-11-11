"""Wire-frame emission for the swarm manager.

Extracted from SwarmManager to keep the manager under its architecture LOC
budget. The mixin owns no state: it reads the manager's ``_write_frame``
callback, ``agents`` map and ``store``, and the room record builders.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from zenagent.app.swarm.room import (
    RoomMessage,
    SwarmStore,
    TaskRecord,
    _message_record,
    _task_record,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from zenagent.app.swarm.manager import SwarmAgent

__all__ = ["SwarmFrames"]


class SwarmFrames:
    """Emit swarm wire frames; mixed into SwarmManager."""

    _write_frame: Callable[[dict], None] | None
    agents: dict[str, SwarmAgent]
    store: SwarmStore

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