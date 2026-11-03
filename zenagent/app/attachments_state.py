"""Images attached with ``/image`` that ride along with the next prompt.

Split out of ``ChatController``: pure list state, no agent or queue access.
The dequeue path hands images back via :meth:`prepend`; the submit path
consumes them via :meth:`take`.
"""

from __future__ import annotations

from zenagent.types import ImageContent

__all__ = ["Attachments"]


class Attachments:
    """Ordered pending images, in composer order."""

    def __init__(self) -> None:
        self._blocks: list[ImageContent] = []

    def all(self) -> list[ImageContent]:
        return list(self._blocks)

    def add(self, block: ImageContent) -> None:
        self._blocks.append(block)

    def remove(self, index: int) -> bool:
        if 0 <= index < len(self._blocks):
            del self._blocks[index]
            return True
        return False

    def clear(self) -> bool:
        if not self._blocks:
            return False
        self._blocks.clear()
        return True

    def prepend(self, blocks: list[ImageContent]) -> None:
        self._blocks[:0] = blocks

    def take(self, explicit: list[ImageContent] | None) -> list[ImageContent]:
        """The blocks to send: ``explicit`` wins; otherwise snapshot + clear."""
        if explicit is not None:
            return list(explicit)
        blocks = list(self._blocks)
        self._blocks.clear()
        return blocks
