"""Mention routing and the structural gates that bound autonomous chains.

The router is pure data plus store appends: it turns ``@role`` mentions into
queued tasks and refuses admissions that would exceed chain depth, chain cost
or an agent's inbox. Rejections are returned, never posted — the manager owns
room notices.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from zenagent.app.swarm.room import RoomMessage, SwarmStore, TaskRecord
from zenagent.app.swarm.workitems import WorkItemStore

__all__ = ["Admission", "Router"]

DEFAULT_MAX_CHAIN_DEPTH = 8
DEFAULT_MAX_CHAIN_COST = 5.0
DEFAULT_INBOX_CAP = 16


@dataclass
class Admission:
    accepted: list[TaskRecord] = field(default_factory=list)
    rejected: list[tuple[str, str]] = field(default_factory=list)


class Router:
    def __init__(
        self,
        store: SwarmStore,
        role_ids: set[str],
        *,
        work_items: WorkItemStore | None = None,
        max_chain_depth: int = DEFAULT_MAX_CHAIN_DEPTH,
        max_chain_cost: float = DEFAULT_MAX_CHAIN_COST,
        inbox_cap: int = DEFAULT_INBOX_CAP,
    ) -> None:
        self.store = store
        self.role_ids = set(role_ids)
        #: Optional: chain rebasing needs to know which work item a round
        #: belongs to, but a router with no work items (older callers, tests)
        #: keeps the single-chain behaviour.
        self.work_items = work_items
        self.max_chain_depth = max_chain_depth
        self.max_chain_cost = max_chain_cost
        self.inbox_cap = inbox_cap

    def admit(
        self,
        message: RoomMessage,
        *,
        inbox_sizes: dict[str, int],
        work_item_id: str | None = None,
    ) -> Admission:
        admission = Admission()
        source = self.store.tasks.get(message.task_id) if message.task_id else None
        # A round's chain is the delegation lineage *inside one work item*. When
        # a round lands in a different work item than its parent, the lineage is
        # cut: inheriting it would hand a brand-new item a chain that is already
        # deep and near its spend cap, i.e. the gate would refuse the first round
        # of a fresh job (see the spec's rejection of "chain == work item").
        crosses = (
            work_item_id is not None
            and source is not None
            and source.work_item_id != work_item_id
        )
        if source is not None and not crosses:
            chain_id = source.chain_id
            chain_cost = self.store.chain_spend.get(chain_id, 0.0)
        else:
            chain_id = self.new_chain_id()
            chain_cost = 0.0
        chain_depth = 1 if crosses else message.chain_depth + 1
        now = int(time.time())

        for role_id in dict.fromkeys(message.mentions):
            if role_id not in self.role_ids:
                continue
            if chain_depth > self.max_chain_depth:
                admission.rejected.append((role_id, "chain_depth"))
                continue
            if chain_cost >= self.max_chain_cost:
                admission.rejected.append((role_id, "chain_cost"))
                continue
            if inbox_sizes.get(role_id, 0) >= self.inbox_cap:
                admission.rejected.append((role_id, "inbox_full"))
                continue
            task = TaskRecord(
                id=self.store.new_id("t"),
                assignee=role_id,
                state="queued",
                source_message_id=message.id,
                created_by_kind=message.author_kind,
                created_by_id=message.author_id,
                chain_id=chain_id,
                chain_depth=chain_depth,
                chain_cost=chain_cost,
                created_at=now,
                updated_at=now,
                work_item_id=work_item_id,
            )
            self.store.append_task(task)
            if self.work_items is not None:
                self.work_items.note_round(work_item_id)
            admission.accepted.append(task)
        return admission

    def new_chain_id(self) -> str:
        return self.store.new_id("c")
