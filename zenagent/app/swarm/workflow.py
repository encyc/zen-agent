"""Work item lifecycle: resolve a round, open one, ask for it to be closed.

Extracted from ``SwarmManager`` to keep that file under its architecture LOC
budget. The rules are pure bookkeeping over the room's message and task logs:
a human mention opens or joins an item, a quote continues a closed one, and an
agent may explicitly open one when a request is a genuinely new job.
"""

from __future__ import annotations

from collections.abc import Callable

from zenagent.agent.protocol import AgentTool
from zenagent.app.swarm.protocol import build_work_tools
from zenagent.app.swarm.room import RoomMessage
from zenagent.app.swarm.workitems import WorkItem, WorkItemStore

__all__ = ["SwarmWorkflow"]

#: Work item titles are one readable line; the panel has no room for more.
MAX_TITLE = 80


class SwarmWorkflow:
    """Open/continue/close-request rules; mixed into SwarmManager."""

    work_items: WorkItemStore
    role_ids: set[str]
    _system_note: Callable[[str], object]
    _emit_work: Callable[[WorkItem], None]

    def _work_title(self, text: str) -> str:
        first = next((line.strip() for line in text.splitlines() if line.strip()), "")
        return (first or "新工单")[:MAX_TITLE]

    def _resolve_work_item(self, message: RoomMessage, ref: str | None) -> str | None:
        """Which work item a newly posted message's rounds belong to.

        ``None`` means ad-hoc: a message that quotes pre-work-item history
        keeps that history's grouping instead of inventing an item for it.
        """
        if not any(role_id in self.role_ids for role_id in message.mentions):
            # ``@you`` notifies the person; it does not open a job.
            return None
        if ref is not None:
            parent = self.work_items.for_message(ref)
            if parent is None:
                return None
            item = self.work_items.items.get(parent)
            if item is not None and item.state == "closed":
                # A closed item is a settled KPI sample: continue in a fresh
                # item and remember where it came from.
                fresh = self._open_item(
                    message.id,
                    self._work_title(message.text),
                    opened_by_kind=message.author_kind,
                    opened_by_id=message.author_id,
                    continued_from=parent,
                )
                return fresh.id
            return parent
        item = self._open_item(
            message.id,
            self._work_title(message.text),
            opened_by_kind=message.author_kind,
            opened_by_id=message.author_id,
        )
        return item.id

    def _open_item(self, root_message_id: str, title: str, **kwargs) -> WorkItem:
        item = self.work_items.open(root_message_id, title, **kwargs)
        self._emit_work(item)
        return item

    def work_open(
        self, reason: str, *, opened_by: str, opened_from: str | None
    ) -> WorkItem:
        """Open a work item for a distinct job (the ``work_open`` tool).

        The card is the item's root message, and ``opened_from`` points at the
        item the agent was working on so a wrongly split job can be merged
        back. Idempotent while the new item has no rounds yet.
        """
        existing = self.work_items.unused(
            opened_from=opened_from, opened_by_id=opened_by
        )
        if existing is not None:
            return existing
        title = self._work_title(reason)
        card = self._system_note(
            f"工单已开：{title}（由 @{opened_by} 发起：{reason.strip()[:200]}）"
        )
        return self._open_item(
            card.id,
            title,
            opened_by_kind="agent",
            opened_by_id=opened_by,
            opened_from=opened_from,
        )

    def work_tools(
        self, *, opened_by: str, opened_from: str | None
    ) -> list[AgentTool]:
        """The two work-item tools this instance's runtime carries."""
        return build_work_tools(
            lambda reason: self.work_open(
                reason, opened_by=opened_by, opened_from=opened_from
            ),
            lambda note: self.request_close(opened_from, note),
        )

    def request_close(self, work_item_id: str | None, note: str) -> WorkItem:
        """Ask the human to accept and close the current item."""
        if not work_item_id:
            raise ValueError("this run is not bound to a work item")
        item = self.work_items.update(work_item_id, state="review")
        self._system_note(
            f"工单请求关闭，待验收：{item.title}（{note.strip()[:200]}）"
        )
        self._emit_work(item)
        return item
