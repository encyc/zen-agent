"""Mode-specific work admission behind one supervisor entry point.

The orchestrators keep their own scheduling policies: zen dispatches onto a
worker session, swarm posts into a room and lets the room assign roles. What
they should not keep is a separate *entry* per mode. A dispatcher is the small
strategy object the supervisor mounts while the orchestrator is alive, so
``supervisor dispatch`` has one contract and the mode-specific shapes stay at
the edge.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

__all__ = [
    "DispatchUnavailable",
    "Dispatcher",
    "SwarmDispatcher",
    "ZenDispatcher",
]


class DispatchUnavailable(RuntimeError):
    """No live orchestrator is mounted for the requested mode."""


class Dispatcher(Protocol):
    """One mode's dispatch strategy, mounted on the project supervisor."""

    mode: str

    async def dispatch(self, **payload: Any) -> dict: ...


class ZenDispatcher:
    """Dispatch a task onto one zen worker session."""

    mode = "zen"

    def __init__(self, fleet: Any) -> None:
        self.fleet = fleet

    async def dispatch(self, *, prompt: str, session: str, **_: Any) -> dict:
        status = await self.fleet.dispatch(str(session), prompt)
        return {
            "dispatched": True,
            "session": str(session),
            "agent": Path(str(session)).stem,
            "status": status,
        }


class SwarmDispatcher:
    """Post work into a swarm room; the room decides the assignee."""

    mode = "swarm"

    def __init__(self, manager: Any) -> None:
        self.manager = manager

    async def dispatch(
        self,
        *,
        prompt: str,
        role: str = "",
        ref: str | None = None,
        images: list[str] | None = None,
        **_: Any,
    ) -> dict:
        if getattr(self.manager, "started", True) is False:
            raise DispatchUnavailable("swarm room is closed")
        if role:
            known = {
                str(view.get("id"))
                for view in self.manager.roles()
                if isinstance(view, dict)
            }
            if role not in known:
                raise ValueError(f"unknown swarm role: {role}")
        tasks = await self.manager.post(
            f"@{role} {prompt}" if role else prompt, ref=ref, images=images
        )
        views = [self.manager.task_view(task) for task in tasks]
        return {
            "dispatched": True,
            "tasks": views,
            "taskIds": [
                str(view["id"]) for view in views if isinstance(view.get("id"), str)
            ],
        }
