"""Instance indexing for the swarm manager.

A run unit is a ``(role, work item)`` pair, not a role: the same agent may be
working two work items at once, each with its own session. This module holds
the small pieces of that identity so the manager stays under its line budget:
the key type, and the idle bookkeeping behind reclamation.

Nothing here knows about runtimes — it is pure data plus a clock.
"""

from __future__ import annotations

import time

__all__ = ["AgentKey", "IdleTracker", "agent_key"]

#: ``(role_id, work_item_id)``. The work item is ``None`` for ad-hoc history,
#: i.e. rounds whose task carried no work item (records written before work
#: items existed, and anything continued from them).
AgentKey = tuple[str, "str | None"]


def agent_key(role_id: str, work_item_id: str | None) -> AgentKey:
    return (role_id, work_item_id or None)


class IdleTracker:
    """Last-touch times per key, and which keys have gone quiet.

    Reclaiming a runtime is only safe when it has no run in flight; the caller
    owns that check (it knows ``_running``). This class answers the other half:
    how long a built instance has been untouched.
    """

    def __init__(self, ttl_s: float = 600.0) -> None:
        self.ttl_s = max(0.0, float(ttl_s))
        self._touched: dict[AgentKey, float] = {}

    def touch(self, key: AgentKey, *, now: float | None = None) -> None:
        self._touched[key] = time.monotonic() if now is None else now

    def forget(self, key: AgentKey) -> None:
        self._touched.pop(key, None)

    def idle(self, key: AgentKey, *, now: float | None = None) -> bool:
        if self.ttl_s <= 0:
            return False
        last = self._touched.get(key)
        if last is None:
            return False
        current = time.monotonic() if now is None else now
        return current - last >= self.ttl_s

    def keys(self) -> list[AgentKey]:
        return list(self._touched)
