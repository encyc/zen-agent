"""Markdown that can be *streamed*: only the growing tail is re-parsed.

``Markdown.update`` throws the document away and re-mounts every block, so a
token stream pays O(document) per delta (in an executor) and leaves the removed
paragraphs answering hit tests for a turn — the click that crashed
``Screen._forward_event``. ``Markdown.append`` re-parses just the uncommitted
tail and updates the last block in place, so the widgets on screen (and under
the pointer) stay alive.

``append`` is not safe to call concurrently: it reads ``_last_parsed_line``
before its own awaitable runs, so two overlapping calls parse the same tail
twice and duplicate blocks. ``stream_text`` therefore keeps a single pump that
renders whatever the newest text is, and always converges because the pump is
only ever started when it is not already running.
"""

from __future__ import annotations

import asyncio

from textual.widgets import Markdown

__all__ = ["StreamingMarkdown"]


class StreamingMarkdown(Markdown):
    """A ``Markdown`` whose text grows mid-turn without a full rebuild."""

    def __init__(self, text: str = "", **kwargs) -> None:
        super().__init__(text, **kwargs)
        #: What the widget has actually parsed; ``append`` is incremental off it.
        self._shown = text
        #: The newest text asked for; the pump renders this, not every delta.
        self._target = text
        self._pump: asyncio.Task[None] | None = None

    def stream_text(self, text: str) -> None:
        """Show ``text``, appending the new tail or rebuilding if it was rewritten."""
        self._target = text
        self._start_pump()

    def on_mount(self) -> None:
        # ``stream_text`` can be called before the widget is in the DOM; the
        # render then waits for the mount instead of touching a DOM that isn't
        # there yet. ``is_mounted`` only flips once the mount finishes.
        self.call_after_refresh(self._start_pump)

    def _start_pump(self) -> None:
        if self._pump is None and self.is_mounted and self._target != self._shown:
            self._pump = asyncio.create_task(self._drain())

    async def wait_for_render(self) -> None:
        """Await the outstanding render, so the DOM can be read deterministically."""
        while self._pump is not None:
            await asyncio.shield(self._pump)

    async def _drain(self) -> None:
        try:
            while self._target != self._shown:
                target = self._target
                if target.startswith(self._shown):
                    await self.append(target[len(self._shown) :])
                else:
                    # The canonical message replaced the deltas (a retry, a
                    # session switch, provider-corrected text): nothing to append.
                    await self.update(target)
                self._shown = target
        finally:
            # Cleared without an await in between, so a ``stream_text`` that
            # arrives later cannot see a dead pump and strand its text.
            self._pump = None
