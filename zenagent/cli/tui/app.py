"""Textual application wiring the app layer to widgets.

Layout follows omp's single-column dock (see
``docs/research/omp-cli-study/02-layout.md``):

    transcript (1fr)  →  status band (1 row)  →  prompt row

There is no bottom status bar: the band *is* the status line, and it sits above
the prompt. The sidebar is opt-in (ctrl+b) and hidden by default.
"""

from __future__ import annotations

from textual.app import App
from textual.containers import Horizontal, Vertical
from textual.events import MouseEvent

from zenagent.app.chat import ChatController
from zenagent.app.commands import AppServices, build_default_commands
from zenagent.app.models import ModelManager
from zenagent.app.runtime import Runtime
from zenagent.app.sessions import SessionManager
from zenagent.cli.tui.widgets.new_content import NewContentBar
from zenagent.cli.tui.widgets.prompt_gutter import PromptGutter
from zenagent.cli.tui.widgets.prompt_input import PromptInput
from zenagent.cli.tui.widgets.sidebar import Sidebar
from zenagent.cli.tui.widgets.status_band import StatusBand
from zenagent.cli.tui.widgets.transcript import TranscriptView

__all__ = ["ZenAgentApp"]


class ZenAgentApp(App):
    """A single-column chat app; no mouse, no animations."""

    CSS_PATH = "theme.tcss"
    TITLE = "ZenAgent"
    BINDINGS = [  # noqa: RUF012 - Textual class API
        ("ctrl+b", "toggle_sidebar", "Info"),
        ("ctrl+c", "interrupt", "Interrupt"),
        ("ctrl+s", "steer", "Steer"),
        ("ctrl+q", "quit_app", "Quit"),
        ("ctrl+x", "clear_queue", "Clear queue"),
        ("ctrl+o", "toggle_tools", "Expand tools"),
        ("end", "jump_to_bottom", "Latest"),
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
        self.exit_summary: str | None = None
        self.models = ModelManager(runtime)
        self.sessions = SessionManager(runtime)
        self.commands = build_default_commands(
            AppServices(
                runtime=runtime, chat=self.chat, models=self.models, sessions=self.sessions
            )
        )

    def action_quit_app(self) -> None:
        status = self.chat.status
        session = getattr(self.runtime.session, "path", None)
        self.exit_summary = (
            f"session: {session} · {status.input_tokens} in / "
            f"{status.output_tokens} out · ${status.cost:.4f}"
        )
        self.exit()

    async def on_event(self, event) -> None:
        # Keyboard-only UI: dropping mouse events avoids Textual 8.2's crashing
        # text-selection path (screen._forward_event with a null container) and
        # matches the spec's explicit "no mouse" requirement.
        if isinstance(event, MouseEvent):
            return
        await super().on_event(event)

    def compose(self):
        with Horizontal(id="root"):
            with Vertical(id="main-column"):
                yield TranscriptView()
                yield NewContentBar()
                with Vertical(id="dock"):
                    yield StatusBand()
                    with Horizontal(id="prompt-row"):
                        yield PromptGutter()
                        yield PromptInput()
            yield Sidebar()

    def on_mount(self) -> None:
        self.query_one(Sidebar).display = False
        self._refresh_status()
        self.query_one(PromptInput).focus()

    def _on_change(self) -> None:
        self.call_after_refresh(self._refresh_status)

    def _refresh_status(self) -> None:
        status = self.chat.status
        width = self.size.width
        for band in self.query(StatusBand):
            band.update_status(status, width)
        for log in self.query(TranscriptView):
            log.sync(self.chat.transcript, status)

    def on_resize(self, event) -> None:
        self._refresh_status()

    def on_transcript_view_pending_changed(
        self, message: TranscriptView.PendingChanged
    ) -> None:
        bar = self.query_one(NewContentBar)
        if message.pending:
            bar.show_marker()
        else:
            bar.hide_marker()

    def action_jump_to_bottom(self) -> None:
        for transcript in self.query(TranscriptView):
            transcript.scroll_to_bottom()

    def on_prompt_input_resized(self, message: PromptInput.Resized) -> None:
        for gutter in self.query(PromptGutter):
            gutter.styles.height = message.rows

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
            for log in self.query(TranscriptView):
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
                for log in self.query(TranscriptView):
                    log.reset()
                self.chat.reload_transcript()
                self._refresh_status()

        self.push_screen(SessionPicker(self.sessions.entries()), selected)

    async def on_prompt_input_submitted(self, message: PromptInput.Submitted) -> None:
        text = message.text
        if text.startswith("/"):
            name, _, argument = text[1:].partition(" ")
            if self.commands.get(name) is not None:
                await self._dispatch_command(name, argument)
                return
            for log in self.query(TranscriptView):
                log.append_notice(f"unknown command: /{name}")
            return
        await self.chat.submit(text)

    async def _dispatch_command(self, name: str, argument: str) -> None:
        result = await self.commands.dispatch(name, argument)
        if result.screen == "model-picker":
            self.open_model_picker()
        elif result.screen == "session-picker":
            self.open_session_picker()
        elif result.quit:
            self.action_quit_app()
        elif result.message:
            for log in self.query(TranscriptView):
                log.append_notice(result.message)

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

    def action_toggle_tools(self) -> None:
        """Batch expand/collapse; single cards keep Enter/space (omp parity)."""
        from zenagent.cli.tui.widgets.tool_card import ToolCard

        cards = list(self.query(ToolCard))
        if not cards:
            return
        expand = any(card.collapsed for card in cards)
        for card in cards:
            card.set_collapsed(not expand)
