"""Textual application wiring the app layer to widgets."""

from __future__ import annotations

from textual.app import App

from zenagent.app.chat import ChatController
from zenagent.app.commands import AppServices, build_default_commands
from zenagent.app.models import ModelManager
from zenagent.app.runtime import Runtime
from zenagent.app.sessions import SessionManager
from zenagent.cli.tui.screens.main import MainScreen
from zenagent.cli.tui.widgets.chat_log import ChatLog
from zenagent.cli.tui.widgets.prompt_input import PromptInput
from zenagent.cli.tui.widgets.sidebar import Sidebar
from zenagent.cli.tui.widgets.status_bar import StatusBar

__all__ = ["ZenAgentApp"]


class ZenAgentApp(App):
    """A single-screen chat app; no mouse, no animations."""

    CSS_PATH = "theme.tcss"
    TITLE = "ZenAgent"
    BINDINGS = [  # noqa: RUF012 - Textual class API
        ("ctrl+b", "toggle_sidebar", "Sidebar"),
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
        self.models = ModelManager(runtime)
        self.sessions = SessionManager(runtime)
        self.commands = build_default_commands(
            AppServices(
                runtime=runtime, chat=self.chat, models=self.models, sessions=self.sessions
            )
        )

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
        for sidebar in self.query(Sidebar):
            sidebar.refresh_contents(self.chat, self.models, self.sessions)
            sidebar.apply_width(self.size.width)

    def on_resize(self, event) -> None:
        for sidebar in self.query(Sidebar):
            sidebar.apply_width(event.size.width)

    def action_toggle_sidebar(self) -> None:
        for sidebar in self.query(Sidebar):
            sidebar.display = not sidebar.display

    def get_system_commands(self, screen):
        from textual.app import SystemCommand

        yield from super().get_system_commands(screen)
        for command in self.commands.names():
            yield SystemCommand(
                command.name,
                command.description,
                lambda name=command.name: self._run_command(name),
            )

    def _run_command(self, name: str) -> None:
        if name == "model":
            self.open_model_picker()
        elif name == "sessions":
            self.open_session_picker()
        elif name == "quit":
            self.exit()
        else:
            for log in self.query(ChatLog):
                log.append_notice(f"/{name} is not available in the TUI yet")

    def open_model_picker(self) -> None:
        from zenagent.cli.tui.screens.modals import ModelPicker

        async def selected(model_id: str | None) -> None:
            if model_id:
                result = self.models.switch(model_id)
                if not result.ok:
                    self.notify(result.reason or "switch failed")
                self._refresh_status()

        self.push_screen(ModelPicker(self.models.entries()), selected)

    def open_session_picker(self) -> None:
        from zenagent.cli.tui.screens.modals import SessionPicker

        async def selected(path) -> None:
            if path is not None:
                await self.sessions.resume(path)
                self.chat.reload_transcript()
                self._refresh_status()

        self.push_screen(SessionPicker(self.sessions.entries()), selected)

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
