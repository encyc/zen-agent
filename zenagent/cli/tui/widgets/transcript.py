"""Transcript area: user/assistant messages, tool cards and usage rows.

The transcript is the only flexible region: the dock below it keeps its height
and the terminal's bottom row is always the prompt.
"""

from __future__ import annotations

from textual.containers import VerticalScroll
from textual.message import Message
from textual.widgets import Markdown

from zenagent.app.views import MessageView, RunStatus, ToolCallView
from zenagent.cli.tui.widgets.rows import NoticeRow, ThinkingRow, UsageRow, UserBlock
from zenagent.cli.tui.widgets.tool_card import ToolCard
from zenagent.cli.tui.widgets.welcome import WelcomeBlock

__all__ = ["TranscriptView"]


class TranscriptView(VerticalScroll):
    """Renders the ChatController transcript, appending incrementally.

    omp renders straight into the terminal's scrollback; Textual cannot, so the
    transcript has to re-implement the one behaviour that mattered: follow new
    output only while the user is at the bottom. Scrolling up pins the view and
    raises a "new output" marker instead of yanking it back down.
    """

    class PendingChanged(Message):
        """Posted when new output is waiting below a pinned viewport."""

        def __init__(self, pending: bool) -> None:
            super().__init__()
            self.pending = pending

    def __init__(self) -> None:
        super().__init__(id="transcript")
        self._count = 0
        self._assistant_widgets: list[Markdown] = []
        self._thinking_rows: dict[int, ThinkingRow] = {}
        self._usage_shown = 0
        self._stick = True
        self._pending = False
        self._resize_follow = False
        #: Assistant text already pushed into its Markdown widget, by slot.
        self._assistant_text: dict[int, str] = {}
        #: ToolCard widgets by call id (a DOM query per sync was O(n)).
        self._cards_by_id: dict[str, ToolCard] = {}
        #: Rendered status per call id, so unchanged cards are not repainted.
        self._card_status: dict[str, str] = {}
        self._follow_pending = False

    def on_mount(self) -> None:
        self.mount(WelcomeBlock())

    @property
    def pending(self) -> bool:
        return self._pending

    def watch_scroll_y(self, old: float, new: float) -> None:
        # ScrollView.watch_scroll_y is what moves the scrollbar thumb and asks
        # for a repaint. Overriding it without this call scrolls the offsets
        # while the screen stays frozen (the "wheel does nothing" report).
        super().watch_scroll_y(old, new)
        # Only ever *re-pin* here. Layout-driven changes (the container growing
        # while content stays put) also move scroll_y, and treating those as
        # "the user scrolled away" is what stopped the view from following.
        if self._at_bottom():
            self._stick = True
            if self._pending:
                self._set_pending(False)

    def _on_mouse_scroll_up(self, event) -> None:
        self._unpin_for_scroll_up()
        super()._on_mouse_scroll_up(event)
        self.call_after_refresh(self._settle_user_scroll)

    def _on_mouse_scroll_down(self, event) -> None:
        super()._on_mouse_scroll_down(event)
        self.call_after_refresh(self._settle_user_scroll)

    # Scrolling moves the offset on the next layout pass, so the pin has to be
    # decided from intent *before* the move and re-checked after it. Reading
    # scroll_offset immediately after scroll_relative() sees the old value and
    # leaves the view pinned, which made any follow-up frame yank it back to
    # the tail -- the "wheel does nothing" symptom.

    def _unpin_for_scroll_up(self) -> None:
        if self.max_scroll_y > 0 and self.scroll_offset.y > 0:
            self._stick = False

    def _settle_user_scroll(self) -> None:
        self._stick = self._at_bottom()
        if self._stick and self._pending:
            self._set_pending(False)

    def on_resize(self, event) -> None:
        """A resized viewport must not abandon the tail.

        Textual clamps/zeroes scroll offsets during a resize and lays out again
        afterwards, so the follow has to be re-armed *after* the next refresh
        rather than run inline against the old geometry.
        """
        self._resize_follow = True
        self.call_after_refresh(self._follow_after_resize)

    def _follow_after_resize(self) -> None:
        self._resize_follow = False
        if self._stick:
            self._follow_tail()

    def watch_virtual_size(self, old, new) -> None:
        """Content grew (a block mounted): follow it if we are pinned."""
        if self._stick:
            self._follow_tail()

    def _follow_tail(self) -> None:
        """Keep the tail pinned without stacking one retry chain per delta."""
        if not self._stick:
            return
        self.scroll_end(animate=False)
        if not self._follow_pending:
            self._follow_pending = True
            self.call_after_refresh(self._tail_settled)

    def _tail_settled(self) -> None:
        self._follow_pending = False
        if self._stick and self.scroll_offset.y < self.max_scroll_y:
            self._follow_tail()

    def scroll_pages(self, pages: int) -> None:
        """Explicit user scroll: unpins above the tail, re-pins at it."""
        self.scroll_lines(pages * max(1, self.size.height - 2))

    def scroll_lines(self, lines: int) -> None:
        """Scroll by a number of rows (the wheel moves 3 per notch)."""
        if lines < 0:
            self._unpin_for_scroll_up()
        self.scroll_relative(y=lines, animate=False)
        self.call_after_refresh(self._settle_user_scroll)

    def _at_bottom(self) -> bool:
        return self.scroll_offset.y >= self.max_scroll_y - 1

    def _set_pending(self, pending: bool) -> None:
        if pending == self._pending:
            return
        self._pending = pending
        self.post_message(self.PendingChanged(pending))

    def scroll_to_bottom(self) -> None:
        """Return to the live tail and resume following new output."""
        self._stick = True
        self.scroll_end(animate=False)
        self._set_pending(False)

    def sync(self, views: list[MessageView | ToolCallView], status: RunStatus) -> None:
        """Apply the transcript diff.

        Called on every streamed delta, so it must be proportional to what
        changed, not to the transcript length: append new views, repaint the
        live assistant tail and only the tool cards whose status moved.
        """
        grew = len(views) > self._count
        for index in range(self._count, len(views)):
            view = views[index]
            if isinstance(view, MessageView):
                self._append_message(view)
            else:
                self._append_tool(view)
            self._count += 1

        if views:
            tail = views[-1]
            if isinstance(tail, MessageView) and tail.kind == "assistant":
                self._update_assistant(len(self._assistant_widgets) - 1, tail)
            for index, view in enumerate(views):
                if isinstance(view, ToolCallView) and self._card_status.get(view.call_id) != view.status:
                    self._update_tool(view)

        self._maybe_usage(status)
        if self._stick:
            self.scroll_end(animate=False)
            self._follow_tail()
        elif grew:
            self._set_pending(True)

    def _update_assistant(self, slot: int, view: MessageView) -> None:
        if slot < 0 or slot >= len(self._assistant_widgets):
            return
        if self._assistant_text.get(slot) == view.text:
            return
        self._assistant_text[slot] = view.text
        widget = self._assistant_widgets[slot]
        if widget.is_mounted:
            widget.update(view.text)
        self._sync_thinking(slot, view.thinking)

    # -- appends ------------------------------------------------------------

    def _append_message(self, view: MessageView) -> None:
        if view.kind == "user":
            self.mount(UserBlock(view.text))
            return
        if view.thinking:
            # Register the row now so _sync_thinking updates it instead of
            # mounting a second copy when thinking streams in later.
            row = ThinkingRow(view.thinking)
            self._thinking_rows[len(self._assistant_widgets)] = row
            self.mount(row)
        widget = Markdown(view.text, classes="assistant-message")
        self._assistant_widgets.append(widget)
        self.mount(widget)

    def _sync_thinking(self, index: int, thinking: str | None) -> None:
        """Thinking often arrives after the bubble, so mount the row lazily."""
        if not thinking:
            return
        row = self._thinking_rows.get(index)
        if row is None:
            row = ThinkingRow(thinking)
            self._thinking_rows[index] = row
            self.mount(row, before=self._assistant_widgets[index])
            return
        row.set_thinking(thinking)

    def _append_tool(self, view: ToolCallView) -> None:
        card = ToolCard(view)
        self._cards_by_id[view.call_id] = card
        self._card_status[view.call_id] = view.status
        self.mount(card)

    def _update_tool(self, view: ToolCallView) -> None:
        card = self._cards_by_id.get(view.call_id)
        if card is None:
            return
        self._card_status[view.call_id] = view.status
        card.view = view
        card.refresh_view()

    def _maybe_usage(self, status: RunStatus) -> None:
        if status.running or not status.input_tokens:
            return
        if status.input_tokens == self._usage_shown:
            return
        self._usage_shown = status.input_tokens
        self.mount(UsageRow(status))

    def reset(self) -> None:
        """Drop every rendered widget (used when a session is resumed)."""
        self._stick = True
        self._set_pending(False)
        for child in list(self.children):
            child.remove()
        self._count = 0
        self._assistant_widgets.clear()
        self._assistant_text.clear()
        self._thinking_rows.clear()
        self._cards_by_id.clear()
        self._card_status.clear()
        self._usage_shown = 0
        self.mount(WelcomeBlock())

    def append_notice(self, text: str, *, error: bool = False) -> None:
        self.mount(NoticeRow(text, error=error))
        self.scroll_end(animate=False)
