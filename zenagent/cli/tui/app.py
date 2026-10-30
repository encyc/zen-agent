"""Textual application wiring the app layer to widgets."""

from __future__ import annotations

from textual.app import App

from zenagent.app.chat import ChatController
from zenagent.app.runtime import Runtime
from zenagent.cli.tui.screens.main import MainScreen
from zenagent.cli.tui.widgets.chat_log import ChatLog
from zenagent.cli.tui.widgets.prompt_input import PromptInput
from zenagent.cli.tui.widgets.status_bar import StatusBar

__all__ = ["ZenAgentApp"]


class ZenAgentApp(App):
    """A single-screen chat app; no mouse, no animations."""

    CSS_PATH = "theme.tcss"
    TITLE = "ZenAgent"
    BINDINGS = [
        ("ctrl+c", "interrupt", "Interrupt"),
        ("ctrl+s", "steer", "Steer"),
        ("ctrl+q", "clear_queue", "Clear queue"),
    ]

    def __init__(
        self,
        runtime: Runtime,
        *,
        chat: ChatController | None = None,
    ) -> None:
        super().__init__()
        self.runtime = runtime
        self.chat = chat or ChatController(runtime, on_change=self._on_change)

    def compose(self):
        yield MainScreen()

    def on_mount(self) -> None:
        self._refresh_status()
        self.query_one(PromptInput).focus()

    def _on_change(self) -> None:
        self.call_after_refresh(self._refresh_status)

    def _refresh_status(self) -> None:
        status = self.chat.status
        for bar in self.query(StatusBar):
            bar.update_status(status)
        for log in self.query(ChatLog):
            log.sync(self.chat.transcript, status)

    async def on_prompt_input_submitted(self, message: PromptInput.Submitted) -> None:
        await self.chat.submit(message.text)

    def action_interrupt(self) -> None:
        if self.chat.status.running:
            self.chat.abort()
            self.notify("interrupted", timeout=2)

    def action_steer(self) -> None:
        prompt = self.query_one(PromptInput)
        text = prompt.text.strip()
        if text and self.chat.status.running:
            prompt.clear()
            self.chat.steer(text)

    def action_clear_queue(self) -> None:
        self.chat.clear_follow_ups()
