"""Role event taps: forward a running role's AgentEvents to a sink.

The manager owns the run bookkeeping (which task is in flight); this module
keeps the wiring small enough that the manager stays under its line budget,
and the "observers are best-effort" contract lives in one place.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from zenagent.agent.agent import Agent
from zenagent.events import AgentEvent

logger = logging.getLogger(__name__)

EventSink = Callable[[str, str, AgentEvent], None]
TaskOf = Callable[[str], "str | None"]
WorkItemOf = Callable[[str], "str | None"]


def tap(
    role_id: str,
    event: AgentEvent,
    *,
    sink: EventSink | None,
    task_id: str | None,
    work_item_id: str | None = None,
) -> None:
    """Forward one event stamped with ``task_id``; a stray tap (no task, a
    rebuild, a reset) or a failing sink is dropped."""
    if sink is None or task_id is None:
        return
    try:
        sink(role_id, task_id, event, work_item_id)
    except TypeError:
        # A sink that predates work items takes three arguments.
        try:
            sink(role_id, task_id, event)
        except Exception:
            logger.debug("swarm event sink failed", exc_info=True)
    except Exception:
        logger.debug("swarm event sink failed", exc_info=True)


def subscribe_events(
    agent: Agent,
    role_id: str,
    *,
    sink: EventSink | None,
    task_of: TaskOf,
    work_item_of: WorkItemOf | None = None,
) -> None:
    """Wire one role runtime's run events to the sink (no-op without one)."""
    if sink is None:
        return
    work_item_of = work_item_of or (lambda _role_id: None)
    agent.subscribe(
        lambda event: tap(
            role_id,
            event,
            sink=sink,
            task_id=task_of(role_id),
            work_item_id=work_item_of(role_id),
        )
    )
