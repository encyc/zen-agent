"""Work item lifecycle: resolve a round, open one, ask for it to be closed.

Extracted from ``SwarmManager`` to keep that file under its architecture LOC
budget. The rules are pure bookkeeping over the room's message and task logs:
a human mention opens or joins an item, a quote continues a closed one, and an
agent may explicitly open one when a request is a genuinely new job.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from zenagent.agent.protocol import AgentTool
from zenagent.app.swarm.protocol import build_work_tools
from zenagent.app.swarm.room import RoomMessage, SwarmStore, TaskRecord
from zenagent.app.swarm.workitems import WorkItem, WorkItemStore, _work_item_record

__all__ = ["SwarmWorkflow"]

#: Work item titles are one readable line; the panel has no room for more.
MAX_TITLE = 80


class SwarmWorkflow:
    """Open/continue/close-request rules; mixed into SwarmManager."""

    work_items: WorkItemStore
    role_ids: set[str]
    store: SwarmStore
    task_view: Callable[[TaskRecord], dict]
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

    # -- panel projections and human actions --------------------------------

    def work_view(self, item: WorkItem) -> dict:
        record = _work_item_record(item)
        record.pop("type", None)
        record.pop("version", None)
        record["rounds"] = item.rounds
        return record

    def works(self) -> list[dict]:
        items = sorted(
            self.work_items.items.values(), key=lambda item: (item.opened_at, item.id)
        )
        return [self.work_view(item) for item in items]

    def adhoc_tasks(self) -> list[dict]:
        """Rounds with no work item: visible, but never scored or in the KPI."""
        tasks = sorted(
            (task for task in self.store.tasks.values() if task.work_item_id is None),
            key=lambda task: (task.created_at, task.id),
        )
        return [self.task_view(task) for task in tasks]

    def work_detail(self, work_item_id: str) -> dict:
        item = self.work_items.items.get(work_item_id)
        if item is None:
            raise ValueError(f"unknown work item {work_item_id}")
        rounds = sorted(
            (
                task
                for task in self.store.tasks.values()
                if task.work_item_id == work_item_id
            ),
            key=lambda task: (task.created_at, task.id),
        )
        return {
            "work": self.work_view(item),
            "rounds": [self.task_view(task) for task in rounds],
        }

    def score_work(
        self, work_item_id: str, score: int | None, note: str | None
    ) -> WorkItem:
        item = self.work_items.score(work_item_id, score, note)
        self._emit_work(item)
        return item

    def close_work(
        self,
        work_item_id: str,
        *,
        score: int | None = None,
        note: str | None = None,
    ) -> WorkItem:
        item = self.work_items.update(
            work_item_id, state="closed", closed_at=int(time.time())
        )
        if score is not None or note is not None:
            item = self.work_items.score(work_item_id, score, note)
        self._emit_work(item)
        return item

    def reopen_work(self, work_item_id: str) -> WorkItem:
        item = self.work_items.update(work_item_id, state="open")
        self._emit_work(item)
        return item

    def merge_works(self, source_id: str, target_id: str) -> WorkItem:
        item = self.work_items.merge(source_id, target_id)
        self._emit_work(item)
        return item

    def rename_work(self, work_item_id: str, title: str) -> WorkItem:
        clean = title.strip()
        if not clean:
            raise ValueError("title must not be empty")
        item = self.work_items.update(work_item_id, title=clean[:200])
        self._emit_work(item)
        return item
