"""Transcript area: user/assistant messages, tool cards and usage rows.

The transcript is the only flexible region: the dock below it keeps its height
and the terminal's bottom row is always the prompt.
"""

from __future__ import annotations

from rich.text import Text
from textual.containers import VerticalScroll
from textual.widgets import Collapsible, Markdown, Static

from zenagent.app.views import MessageView, RunStatus, ToolCallView
from zenagent.cli.tui.widgets.tool_card import ToolCard
from zenagent.cli.tui.widgets.welcome import WelcomeBlock

__all__ = ["TranscriptView"]


class TranscriptView(VerticalScroll):
    """Renders the ChatController transcript, appending incrementally."""

    def __init__(self) -> None:
        super().__init__(id="transcript")
        self._count = 0
        self._assistant_widgets: list[Markdown] = []
        self._usage_shown = 0

    def on_mount(self) -> None:
        self.mount(WelcomeBlock())

    def sync(self, views: list[MessageView | ToolCallView], status: RunStatus) -> None:
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
                assistant_index += 1
        self._maybe_usage(status)
        self.scroll_end(animate=False)

    # -- appends ------------------------------------------------------------

    def _append_message(self, view: MessageView) -> None:
        if view.kind == "user":
            text = Text()
            text.append("› ", style="#d9a86c")
            text.append(view.text, style="#d7e0e8")
            self.mount(Static(text, classes="user-message"))
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
        widget = Markdown(view.text, classes="assistant-message")
        self._assistant_widgets.append(widget)
        self.mount(widget)

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
        self.mount(
            Static(
                f"done · in {status.input_tokens} / out {status.output_tokens} · "
                f"cache {status.cache_read} · ${status.cost:.4f}",
                classes="usage-row",
            )
        )

    def reset(self) -> None:
        """Drop every rendered widget (used when a session is resumed)."""
        for child in list(self.children):
            child.remove()
        self._count = 0
        self._assistant_widgets.clear()
        self._usage_shown = 0
        self.mount(WelcomeBlock())

    def append_notice(self, text: str) -> None:
        self.mount(Static(text, classes="notice"))
        self.scroll_end(animate=False)
