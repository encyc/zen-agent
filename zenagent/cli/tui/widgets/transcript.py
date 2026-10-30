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

    def on_mount(self) -> None:
        self.mount(WelcomeBlock())

    @property
    def pending(self) -> bool:
        return self._pending

    def watch_scroll_y(self, old: float, new: float) -> None:
        # Only ever *re-pin* here. Layout-driven changes (the container growing
        # while content stays put) also move scroll_y, and treating those as
        # "the user scrolled away" is what stopped the view from following.
        if self._at_bottom():
            self._stick = True
            if self._pending:
                self._set_pending(False)

    def watch_virtual_size(self, old, new) -> None:
        """Content grew (a block mounted): follow it if we are pinned."""
        if self._stick:
            self._follow_tail()

    def _follow_tail(self, attempts: int = 4) -> None:
        """Keep the tail pinned across the layout frames a new block costs."""
        if not self._stick:
            return
        self.scroll_end(animate=False)
        if attempts > 1:
            self.call_after_refresh(lambda: self._follow_tail(attempts - 1))

    def scroll_pages(self, pages: int) -> None:
        """Explicit user scroll: unpins above the tail, re-pins at it."""
        self.scroll_relative(y=pages * max(1, self.size.height - 2), animate=False)
        self._stick = self._at_bottom()
        if self._stick and self._pending:
            self._set_pending(False)

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
        before = self._count
        for index in range(self._count, len(views)):
            view = views[index]
            if isinstance(view, MessageView):
                self._append_message(view)
            else:
                self._append_tool(view)
            self._count += 1
        assistant_index = 0
        for view in views:
            if isinstance(view, ToolCallView) and view.status != "running":
                self._update_tool(view)
            elif isinstance(view, MessageView) and view.kind == "assistant":
                if assistant_index < len(self._assistant_widgets):
                    self._assistant_widgets[assistant_index].update(view.text)
                    self._sync_thinking(assistant_index, view.thinking)
                assistant_index += 1
        grew = len(views) > before or self.query(UsageRow) and self._usage_shown
        self._maybe_usage(status)
        if self._stick:
            self.scroll_end(animate=False)
            # Content is laid out after this callback; follow again once the
            # new height is known, otherwise the tail stays one screen behind.
            self.call_after_refresh(self._follow_tail)
        elif grew:
            self._set_pending(True)

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
        self.mount(ToolCard(view))

    def _update_tool(self, view: ToolCallView) -> None:
        for card in self.query(ToolCard):
            if card.view.call_id == view.call_id:
                card.view = view
                card.refresh_view()
                return

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
        self._thinking_rows.clear()
        self._usage_shown = 0
        self.mount(WelcomeBlock())

    def append_notice(self, text: str, *, error: bool = False) -> None:
        self.mount(NoticeRow(text, error=error))
        self.scroll_end(animate=False)
