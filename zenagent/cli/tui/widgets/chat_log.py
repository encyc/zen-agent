"""Transcript area: user/assistant messages, tool cards and usage rows."""

from __future__ import annotations

from textual.containers import VerticalScroll
from textual.widgets import Collapsible, Markdown, Static

from zenagent.app.views import MessageView, RunStatus, ToolCallView
from zenagent.cli.tui.widgets.tool_card import ToolCard

__all__ = ["ChatLog"]


class ChatLog(VerticalScroll):
    """Renders the ChatController transcript, appending incrementally."""

    def __init__(self) -> None:
        super().__init__(id="chat")
        self._count = 0
        self._usage_shown = 0

    def sync(self, views: list[MessageView | ToolCallView], status: RunStatus) -> None:
        for index in range(self._count, len(views)):
            view = views[index]
            if isinstance(view, MessageView):
                self._append_message(view)
            else:
                self._append_tool(view)
            self._count += 1
        for index, view in enumerate(views):
            if isinstance(view, ToolCallView) and view.status != "running":
                self._update_tool(view)
            elif isinstance(view, MessageView) and view.kind == "assistant":
                self._update_message(index, view)
        self._maybe_usage(status)
        self.scroll_end(animate=False)

    # -- appends ------------------------------------------------------------

    def _append_message(self, view: MessageView) -> None:
        if view.kind == "user":
            self.mount(Static(f"› {view.text}", classes="user-message"))
            return
        if view.thinking:
            self.mount(
                Collapsible(
                    Static(view.thinking),
                    title="thinking",
                    collapsed=True,
                    classes="thinking",
                )
            )
        self.mount(Markdown(view.text, classes="assistant-message"))

    def _append_tool(self, view: ToolCallView) -> None:
        self.mount(ToolCard(view))

    def _update_tool(self, view: ToolCallView) -> None:
        for card in self.query(ToolCard):
            if card.view.call_id == view.call_id:
                card.view = view
                card.refresh_view()
                return

    def _update_message(self, index: int, view: MessageView) -> None:
        messages = list(self.query(Markdown))
        if index < len(messages):
            messages[index].update(view.text)

    def _maybe_usage(self, status: RunStatus) -> None:
        if status.running or not status.input_tokens:
            return
        if status.input_tokens == self._usage_shown:
            return
        self._usage_shown = status.input_tokens
        self.mount(
            Static(
                f"done · in {status.input_tokens} / out {status.output_tokens} · "
                f"cache {status.cache_read} · ${status.cost:.4f}",
                classes="usage-row",
            )
        )

    def append_notice(self, text: str) -> None:
        self.mount(Static(text, classes="notice"))
        self.scroll_end(animate=False)
