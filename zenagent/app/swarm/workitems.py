"""Work items: the resumable unit of work a swarm room is organised around.

A work item is a *thread* in the room plus a thin append-only side table. It
owns three things a task never did: the context boundary (one session per
(agent, work item)), the acceptance mount point (a human score), and the KPI
sample. Tasks stay the run record — a work item holds many rounds.

Records follow the same contract as ``room.jsonl`` / ``tasks.jsonl``: appended
lines, last one per id wins, a torn line is skipped with a warning and never
raises. Replay is therefore identical to a fresh write.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace

from zenagent.app.swarm.room import SwarmStore

__all__ = [
    "WORK_STATES",
    "WorkItem",
    "WorkItemStore",
]

#: Lifecycle states, in escalation order. ``closed`` is terminal: a later round
#: opens a new item that records ``continued_from`` instead of reviving this one.
WORK_STATES: tuple[str, str, str] = ("open", "review", "closed")

_WORK_TRANSITIONS: dict[str, set[str]] = {
    "open": {"review", "closed"},
    "review": {"open", "closed"},
    "closed": set(),
}

SCORE_MIN = 1
SCORE_MAX = 5


@dataclass
class WorkItem:
    id: str
    root_message_id: str
    title: str = ""
    opened_by_kind: str = "user"
    opened_by_id: str = ""
    opened_at: int = 0
    state: str = "open"
    score: int | None = None
    score_note: str | None = None
    closed_at: int | None = None
    merged_into: str | None = None
    opened_from: str | None = None
    continued_from: str | None = None
    updated_at: int = 0
    #: Round count, derived on load and on every append; never stored.
    rounds: int = field(default=0, compare=False)


def _now() -> int:
    return int(time.time())


def _work_item_record(item: WorkItem) -> dict:
    return {
        "type": "work_item",
        "version": 1,
        "id": item.id,
        "rootMessageId": item.root_message_id,
        "title": item.title,
        "openedBy": {"kind": item.opened_by_kind, "id": item.opened_by_id},
        "openedAt": item.opened_at,
        "state": item.state,
        "score": item.score,
        "scoreNote": item.score_note,
        "closedAt": item.closed_at,
        "mergedInto": item.merged_into,
        "openedFrom": item.opened_from,
        "continuedFrom": item.continued_from,
        "updatedAt": item.updated_at,
    }


def _apply_work_item(raw: dict) -> WorkItem | None:
    try:
        opened_by = raw.get("openedBy")
        if not isinstance(opened_by, dict):
            opened_by = {}
        state = str(raw.get("state") or "open")
        if state not in WORK_STATES:
            return None
        score = raw.get("score")
        return WorkItem(
            id=str(raw["id"]),
            root_message_id=str(raw.get("rootMessageId") or ""),
            title=str(raw.get("title") or ""),
            opened_by_kind=str(opened_by.get("kind") or "user"),
            opened_by_id=str(opened_by.get("id") or ""),
            opened_at=int(raw.get("openedAt") or 0),
            state=state,
            score=int(score) if score is not None else None,
            score_note=raw.get("scoreNote"),
            closed_at=int(raw["closedAt"]) if raw.get("closedAt") else None,
            merged_into=raw.get("mergedInto"),
            opened_from=raw.get("openedFrom"),
            continued_from=raw.get("continuedFrom"),
            updated_at=int(raw.get("updatedAt") or 0),
        )
    except (KeyError, TypeError, ValueError):
        return None


class WorkItemStore:
    """Append-only work item table living beside the room and task logs."""

    def __init__(self, store: SwarmStore) -> None:
        self.store = store
        self.items: dict[str, WorkItem] = {}
        self.warnings: list[str] = []
        #: Ids that already have at least one accepted round (derived).
        self._with_rounds: set[str] = set()

    @property
    def path(self):
        return self.store.root / "work_items.jsonl"

    def load(self) -> None:
        self.items = {}
        self.warnings = []
        # The replay helper reports torn lines on the room store, which the
        # manager already surfaces; mirror them here so a caller can check this
        # table alone.
        before = len(self.store.warnings)
        self.store._replay(self.path, self._apply)
        self.warnings = list(self.store.warnings[before:])

    def _apply(self, raw: dict) -> None:
        item = _apply_work_item(raw)
        if item is None:
            self.warnings.append(f"{self.path.name}: skipped work item line")
            return
        self.items[item.id] = item

    # -- writes -------------------------------------------------------------

    def open(
        self,
        root_message_id: str,
        title: str,
        *,
        opened_by_kind: str,
        opened_by_id: str,
        opened_from: str | None = None,
        continued_from: str | None = None,
    ) -> WorkItem:
        now = _now()
        item = WorkItem(
            id=self.store.new_id("w"),
            root_message_id=root_message_id,
            title=title.strip()[:200],
            opened_by_kind=opened_by_kind,
            opened_by_id=opened_by_id,
            opened_at=now,
            opened_from=opened_from,
            continued_from=continued_from,
            updated_at=now,
        )
        self._append(item)
        return item

    def update(self, work_item_id: str, **fields) -> WorkItem:
        current = self.items.get(work_item_id)
        if current is None:
            raise ValueError(f"unknown work item {work_item_id}")
        target = fields.get("state")
        if target is not None and target != current.state:
            allowed = _WORK_TRANSITIONS.get(current.state, set())
            if target not in allowed:
                raise ValueError(
                    f"work item {work_item_id}: illegal transition "
                    f"{current.state!r} -> {target!r}"
                )
        if "closed_at" in fields and fields["closed_at"] is not None:
            fields.setdefault("state", "closed")
        updated = replace(current, updated_at=_now(), **fields)
        self._append(updated)
        return updated

    def score(self, work_item_id: str, score: int | None, note: str | None) -> WorkItem:
        if score is not None:
            if not isinstance(score, int) or isinstance(score, bool):
                raise ValueError("score must be an integer 1-5 or null")
            if not SCORE_MIN <= score <= SCORE_MAX:
                raise ValueError(f"score must be {SCORE_MIN}-{SCORE_MAX} or null")
        current = self.items.get(work_item_id)
        if current is None:
            raise ValueError(f"unknown work item {work_item_id}")
        return self.update(work_item_id, score=score, score_note=note)

    def merge(self, source_id: str, target_id: str) -> WorkItem:
        """Point one item's bookkeeping at another; sessions are never merged."""
        if source_id == target_id:
            raise ValueError("cannot merge a work item into itself")
        if target_id not in self.items:
            raise ValueError(f"unknown work item {target_id}")
        return self.update(source_id, merged_into=target_id)

    # -- queries ------------------------------------------------------------

    def for_message(self, message_id: str) -> str | None:
        """The work item a room message belongs to, by root or by a round.

        ``None`` means "ad-hoc": the message predates work items or came from a
        round whose task carried no work item.
        """
        for item in self.items.values():
            if item.root_message_id == message_id:
                return item.id
        for task in self.store.tasks.values():
            if task.work_item_id and message_id in (
                task.source_message_id,
                task.result_message_id,
            ):
                return task.work_item_id
        return None

    def note_round(self, work_item_id: str | None) -> None:
        if work_item_id:
            self._with_rounds.add(work_item_id)

    def _append(self, item: WorkItem) -> None:
        self.store._append_line(self.path, _work_item_record(item))
        self.items[item.id] = item
