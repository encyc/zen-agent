"""The pending queue: queued follow-ups and steering mirrors, in creation order.

Split out of ``ChatController`` as pure view-model state: no agent access, no
attachment semantics (the controller owns those and passes resolved blocks in).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from zenagent.app.views import PendingLane, PendingMessage
from zenagent.types import ImageContent

__all__ = ["PendingItem", "PendingQueue"]


@dataclass
class PendingItem:
    """One pending message plus the images that travel with it."""

    message: PendingMessage
    images: list[ImageContent] = field(default_factory=list)


class PendingQueue:
    """Ordered pending entries across two lanes: ``queue`` and ``steer``."""

    def __init__(self) -> None:
        self._items: list[PendingItem] = []
        self._next_id = 1

    @property
    def items(self) -> list[PendingMessage]:
        return [item.message for item in self._items]

    def count(self, lane: PendingLane) -> int:
        return sum(1 for item in self._items if item.message.lane == lane)

    def queue(self, text: str, images: list[ImageContent]) -> PendingMessage:
        """Append a ``queue``-lane entry (waits for the run to end)."""
        return self.add(text, images, "queue")

    def add(
        self, text: str, images: list[ImageContent] | None, lane: PendingLane
    ) -> PendingMessage:
        """Append one entry and return its view-model snapshot."""
        blocks = list(images or [])
        message = PendingMessage(
            id=self._next_id,
            lane=lane,
            text=text,
            images=len(blocks),
            created_at=time.time(),
        )
        self._next_id += 1
        self._items.append(PendingItem(message=message, images=blocks))
        return message

    def clear(self) -> int:
        """Drop every pending message (both lanes); returns how many went."""
        cleared = len(self._items)
        self._items.clear()
        return cleared

    def take_next_queue_item(self) -> PendingItem | None:
        """Pop the oldest queue-lane entry (the next turn), if any."""
        for index, item in enumerate(self._items):
            if item.message.lane == "queue":
                return self._items.pop(index)
        return None

    def dequeue_latest(self) -> tuple[str, list[ImageContent]] | None:
        """Pop the most recently queued follow-up: ``(text, images)``, or None.

        omp's dequeue semantics: the last thing you queued is the one you most
        likely want to edit, so pop from the end.
        """
        for index in range(len(self._items) - 1, -1, -1):
            item = self._items[index]
            if item.message.lane != "queue":
                continue
            del self._items[index]
            return item.message.text, item.images
        return None

    def remove_lane(self, lane: PendingLane) -> None:
        self._items = [item for item in self._items if item.message.lane != lane]

    def find(self, pending_id: int) -> PendingItem | None:
        return next(
            (item for item in self._items if item.message.id == pending_id),
            None,
        )

    def remove(self, item: PendingItem) -> None:
        self._items.remove(item)
