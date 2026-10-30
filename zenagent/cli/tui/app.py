"""Textual application wiring the app layer to widgets.

Layout follows omp's single-column dock (see
``docs/research/omp-cli-study/02-layout.md``):

    transcript (1fr)  →  status band (1 row)  →  prompt row

There is no bottom status bar: the band *is* the status line, and it sits above
the prompt. The sidebar is opt-in (ctrl+b) and hidden by default.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from pathlib import Path

from textual.app import App
from textual.containers import Horizontal, Vertical
from textual.events import MouseScrollDown, MouseScrollUp

from zenagent.app.chat import ChatController
from zenagent.app.commands import AppServices, build_default_commands
from zenagent.app.models import ModelManager
from zenagent.app.runtime import Runtime
from zenagent.app.sessions import SessionManager
from zenagent.cli.tui.history import PromptHistory
from zenagent.cli.tui.widgets.command_hints import CommandHints
from zenagent.cli.tui.widgets.error_banner import ErrorBanner
from zenagent.cli.tui.widgets.new_content import NewContentBar
from zenagent.cli.tui.widgets.prompt_gutter import PromptGutter
from zenagent.cli.tui.widgets.prompt_input import PromptInput
from zenagent.cli.tui.widgets.sidebar import Sidebar
from zenagent.cli.tui.widgets.status_band import StatusBand
from zenagent.cli.tui.widgets.transcript import TranscriptView
from zenagent.cli.tui.widgets.work_indicator import WorkIndicator

__all__ = ["DOUBLE_ESCAPE_SECONDS", "ZenAgentApp"]

#: Two empty-draft Escapes this close together mean "rewind the last turn"
#: (omp's Esc chain level 13); a slower second press just interrupts nothing.
DOUBLE_ESCAPE_SECONDS = 0.6


class ZenAgentApp(App):
    """A single-column chat app; no mouse, no animations."""

    CSS_PATH = "theme.tcss"
    TITLE = "ZenAgent"
    # Textual's text selection is the one mouse path that crashed with Markdown
    # blocks (a Screen._forward_event bug); we never select text, so switch it
    # off and keep every other mouse event.
    ALLOW_SELECT = False
    # omp's chord set (``docs/research/omp-cli-study/01-interaction-hierarchy.md``):
    # Esc interrupts, ctrl+c clears the draft, ctrl+d exits, ctrl+q queues.
    BINDINGS = [  # noqa: RUF012 - Textual class API
        ("escape", "interrupt", "Interrupt"),
        ("ctrl+c", "clear_draft", "Clear draft"),
        ("ctrl+d", "quit_app", "Quit"),
        ("ctrl+q", "follow_up", "Follow-up"),
        ("ctrl+p", "cycle_model", "Next model"),
        ("alt+p", "pick_model", "Pick model"),
        ("shift+tab", "cycle_thinking", "Thinking"),
        ("ctrl+t", "toggle_thinking", "Expand thinking"),
        ("ctrl+o", "toggle_tools", "Expand tools"),
        ("ctrl+k", "command_palette", "Commands"),
        ("ctrl+r", "history_search", "History"),
        ("ctrl+b", "toggle_sidebar", "Info"),
        ("ctrl+s", "steer", "Steer"),
        ("ctrl+x", "clear_queue", "Clear queue"),
        ("alt+up", "dequeue", "Dequeue"),
    ]

    def __init__(
        self,
        runtime: Runtime,
        *,
        chat: ChatController | None = None,
        history_path: Path | None = None,
    ) -> None:
        super().__init__()
        self.runtime = runtime
        self.chat = chat or ChatController(runtime, on_change=self._on_change)
        self.history = PromptHistory(history_path)
        self.exit_summary: str | None = None
        self._menus: dict[str, object] = {}
        self._refresh_pending = False
        self._hint_start = 0
        self._hint_prefix = "/"
        self._session_scope = "project"
        self._last_escape_at: float | None = None
        self.command_hints: CommandHints | None = None
        self.models = ModelManager(runtime)
        self.sessions = SessionManager(runtime)
        self.commands = build_default_commands(
            AppServices(
                runtime=runtime, chat=self.chat, models=self.models, sessions=self.sessions
            )
        )

    #: Seconds an ask-mode tool approval may stay open before it counts as Deny.
    approval_timeout_s: float = 120.0

    async def request_tool_approval(self, tool_name: str, arguments: dict) -> str:
        """Show the ask-mode modal; returns ``allow`` / ``always`` / ``deny``.

        The agent tool round is awaited on this future, so the modal blocks that
        tool (not the UI). A timeout resolves to ``deny`` and closes the modal.
        """
        from zenagent.cli.tui.screens.modals import ToolApproval

        screen = ToolApproval(tool_name, arguments)
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()

        def done(choice: str | None) -> None:
            if not future.done():
                future.set_result(choice or "deny")

        self.push_screen(screen, done)
        try:
            return await asyncio.wait_for(future, self.approval_timeout_s)
        except TimeoutError:
            if self.screen is screen:
                self.pop_screen()
            self.notify("approval timed out; denied", timeout=3)
            return "deny"

    def action_quit_app(self) -> None:
        status = self.chat.status
        session = getattr(self.runtime.session, "path", None)
        self.exit_summary = (
            f"session: {session} · {status.input_tokens} in / "
            f"{status.output_tokens} out · ${status.cost:.4f}"
        )
        self.exit()

    async def on_event(self, event) -> None:
        await super().on_event(event)

    # -- pointer ------------------------------------------------------------
    #
    # Mouse is supported the way omp supports it: the wheel scrolls the
    # transcript, clicks activate the things that are clickable (tool cards,
    # the "new output" bar). Text selection stays off (ALLOW_SELECT), which is
    # what used to crash the Markdown path.

    WHEEL_ROWS = 3

    def _on_mouse_scroll_up(self, event: MouseScrollUp) -> None:
        self._scroll_chat(-self.WHEEL_ROWS)

    def _on_mouse_scroll_down(self, event: MouseScrollDown) -> None:
        self._scroll_chat(self.WHEEL_ROWS)

    def _scroll_chat(self, rows: int) -> None:
        for transcript in self.query(TranscriptView):
            transcript.scroll_lines(rows)

    def compose(self):
        with Horizontal(id="root"):
            with Vertical(id="main-column"):
                yield TranscriptView()
                yield NewContentBar()
                with Vertical(id="dock"):
                    yield ErrorBanner()
                    yield CommandHints()
                    yield StatusBand()
                    with Horizontal(id="prompt-row"):
                        yield PromptGutter()
                        yield PromptInput()
                        yield WorkIndicator()
            yield Sidebar()

    def on_mount(self) -> None:
        self.command_hints = self.query_one(CommandHints)
        self.query_one(Sidebar).display = False
        self._refresh_status()
        self.query_one(PromptInput).focus()

    def _on_change(self) -> None:
        # Streaming fires this per delta; coalesce bursts into one repaint.
        if self._refresh_pending:
            return
        self._refresh_pending = True
        self.call_later(self._run_refresh)

    def _run_refresh(self) -> None:
        self._refresh_pending = False
        self._refresh_status()

    def _refresh_status(self) -> None:
        status = self.chat.status
        width = self.size.width
        for band in self.query(StatusBand):
            band.update_status(status, width)
        for log in self.query(TranscriptView):
            log.sync(self.chat.transcript, status)
        for indicator in self.query(WorkIndicator):
            indicator.set_running(status.running)
        for banner in self.query(ErrorBanner):
            banner.set_error(status.last_error)

    def dismiss_error(self) -> None:
        """Click on the error banner: drop it and repaint the dock."""
        self.chat.clear_last_error()
        self._refresh_status()

    def on_resize(self, event) -> None:
        self._refresh_status()

    def on_text_area_changed(self, event) -> None:
        """Keep the suggestion list in sync: command names, then argument values."""
        hints = self.command_hints
        if hints is None:
            return
        text = self.query_one(PromptInput).text
        matches, prefix, start = self._hint_context(text)
        self._hint_prefix = prefix
        self._hint_start = start
        if matches:
            hints.show(matches, prefix=prefix)
        else:
            hints.hide()

    def _hint_context(
        self, text: str
    ) -> tuple[list[tuple[str, str]], str, int]:
        """``(matches, render prefix, replace start)`` for the current draft.

        Before the space we list commands (``/mod`` → mode/model); after it we
        list that command's argument values (``/mode `` → plan/ask/full).
        """
        if "\n" in text or not text.startswith("/"):
            return [], "/", 0
        head, separator, rest = text.partition(" ")
        if not separator:
            needle = text[1:].lower()
            matches = [
                (command.name, command.description)
                for command in self.commands.names()
                if command.name.startswith(needle)
            ]
            return matches, "/", 0
        name = head[1:].lower()
        if " " in rest or self.commands.get(name) is None:
            return [], "/", 0
        needle = rest.strip().lower()
        matches = [
            (value, description)
            for value, description in self._argument_values(name)
            if value.lower().startswith(needle)
        ]
        return matches, "", len(head) + 1

    def _argument_values(self, name: str) -> list[tuple[str, str]]:
        """Fixed vocabulary from the shared table; model ids come from the catalog."""
        from zenagent.app.slash_args import argument_values

        if name == "model":
            return [
                (
                    entry.id,
                    f"{entry.provider} · "
                    + ("ready" if entry.available else entry.reason or "unavailable"),
                )
                for entry in self.models.entries()
            ]
        return argument_values("/" + name)

    def action_hint_next(self) -> None:
        if self.command_hints:
            self.command_hints.move(1)

    def action_hint_prev(self) -> None:
        if self.command_hints:
            self.command_hints.move(-1)

    def action_hint_dismiss(self) -> None:
        if self.command_hints:
            self.command_hints.hide()

    def action_hint_accept(self) -> None:
        hints = self.command_hints
        if hints is None or not hints.active:
            return
        selected = hints.selected
        if not selected:
            return
        prompt = self.query_one(PromptInput)
        if self._hint_prefix == "/":
            prompt.clear()
            prompt.insert(f"/{selected} ")
        else:
            draft = prompt.text
            prompt.clear()
            prompt.insert(draft[: self._hint_start] + selected + " ")
        prompt.focus()
        hints.hide()

    def action_scroll_up(self) -> None:
        for transcript in self.query(TranscriptView):
            transcript.scroll_pages(-1)

    def action_scroll_down(self) -> None:
        for transcript in self.query(TranscriptView):
            transcript.scroll_pages(1)

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
        """Palette entries run the same path as typing the command."""
        self.run_worker(self._dispatch_command(name, ""))

    def open_menu(self, kind: str, factory, callback) -> None:
        """Single-instance menus: a repeat request focuses, never stacks.

        omp models this with ``#openMenus`` and deliberately has no raise API
        (``05-overlays-and-surfaces.md`` §4); one entry per kind is enough here
        because our palettes are all rooted at the same screen.
        """
        if kind in self._menus:
            return
        screen = factory()
        self._menus[kind] = screen

        async def finished(result=None) -> None:
            self._menus.pop(kind, None)
            outcome = callback(result)
            if inspect.isawaitable(outcome):
                await outcome

        self.push_screen(screen, finished)

    def _show_help(self) -> None:
        for log in self.query(TranscriptView):
            log.append_notice("commands (type / to complete):")
            for command in self.commands.names():
                log.append_notice(f"  /{command.name}  {command.description}")

    def open_settings(self) -> None:
        from zenagent.cli.tui.screens.modals import SettingsPicker

        async def selected(choice: str | None) -> None:
            if not choice:
                return
            if choice == "sidebar":
                self.action_toggle_sidebar()
            elif choice == "thinking":
                self.action_cycle_thinking()
            elif choice == "model-keys":
                self.open_model_keys()
            elif choice == "search-keys":
                self.open_search_keys()
            elif choice == "search-provider":
                self.open_search_provider()
            else:
                await self._dispatch_command(choice, "")

        status = self.chat.status
        self.open_menu(
            "settings",
            lambda: SettingsPicker(status.thinking, status.model_id),
            selected,
        )

    # -- runtime settings: API keys and the search provider -----------------

    def _model_providers(self) -> list[tuple[str, str, bool]]:
        from zenagent import settings

        rows = []
        for provider_id, provider in self.runtime.catalog.items():
            configured = settings.get_api_key(provider.api_key_env) is not None
            rows.append((f"{provider_id}", provider.api_key_env, configured))
        return rows

    def _search_providers(self) -> list[tuple[str, str, bool]]:
        from zenagent import settings
        from zenagent.tools.web import LABELS
        from zenagent.tools.web.providers import PROVIDERS

        rows = []
        for name in PROVIDERS:
            env_name = f"{name.upper()}_API_KEY"
            if name in ("bing", "duckduckgo"):
                continue  # keyless
            configured = settings.get_api_key(env_name) is not None
            rows.append((LABELS.get(name, name), env_name, configured))
        return rows

    def open_model_keys(self) -> None:
        from zenagent.cli.tui.screens.secrets import ProviderKeyPicker

        async def selected(env_name: str | None) -> None:
            if env_name:
                self._prompt_secret(env_name, "model provider key")

        self.open_menu(
            "model-keys",
            lambda: ProviderKeyPicker(self._model_providers(), scope="model"),
            selected,
        )

    def open_search_keys(self) -> None:
        from zenagent.cli.tui.screens.secrets import ProviderKeyPicker

        async def selected(env_name: str | None) -> None:
            if env_name:
                self._prompt_secret(env_name, "search provider key")

        self.open_menu(
            "search-keys",
            lambda: ProviderKeyPicker(self._search_providers(), scope="search"),
            selected,
        )

    def open_search_provider(self) -> None:
        from zenagent.cli.tui.screens.secrets import SearchProviderPicker

        async def selected(name: str | None) -> None:
            if not name:
                return
            from zenagent import settings

            settings.set_pref("search_provider", None if name == "auto" else name)
            self.notify(f"search provider: {name}", timeout=3)
            self._refresh_status()

        self.open_menu("search-provider", SearchProviderPicker, selected)

    def _prompt_secret(self, env_name: str, scope: str) -> None:
        from zenagent import settings
        from zenagent.cli.tui.screens.secrets import SecretInput

        async def entered(value: str | None) -> None:
            if not value:
                return
            settings.set_api_key(env_name, value)
            self.notify(f"saved {env_name}", timeout=3)
            self._refresh_status()

        self.push_screen(
            SecretInput(
                title=env_name,
                hint=f"{scope} · enter saves · esc cancels · stored in ~/.zenagent/settings.json",
            ),
            entered,
        )

    def action_pick_model(self) -> None:
        self.open_model_picker()

    def open_model_picker(self) -> None:
        from zenagent.cli.tui.screens.modals import ModelPicker

        async def selected(model_id: str | None) -> None:
            if model_id:
                result = self.models.switch(model_id)
                if not result.ok:
                    self.notify(result.reason or "switch failed")
                self._refresh_status()

        self.open_menu("model", lambda: ModelPicker(self.models.entries()), selected)

    def open_mode_picker(self) -> None:
        from zenagent.app.slash_args import argument_values
        from zenagent.cli.tui.screens.modals import ChoicePicker

        async def selected(mode: str | None) -> None:
            if mode:
                self.runtime.set_mode(mode)
                self.notify(f"mode: {mode}")
                self._refresh_status()

        self.open_menu(
            "mode",
            lambda: ChoicePicker("Mode", argument_values("/mode"), self.runtime.mode),
            selected,
        )

    def open_thinking_picker(self) -> None:
        from zenagent.app.slash_args import argument_values
        from zenagent.cli.tui.screens.modals import ChoicePicker

        agent = self.runtime.agent

        async def selected(level: str | None) -> None:
            if level:
                agent.thinking = None if level == "off" else level
                self.notify(f"thinking: {level}")
                self._refresh_status()

        self.open_menu(
            "thinking",
            lambda: ChoicePicker(
                "Thinking",
                argument_values("/thinking"),
                agent.thinking or "off",
            ),
            selected,
        )

    def open_session_picker(self, scope: str | None = None) -> None:
        from zenagent.cli.tui.screens.modals import SessionPicker

        self._session_scope = scope or self._session_scope
        current_scope = self._session_scope

        async def selected(action) -> None:
            if action is None:
                return
            if action.kind == "toggle-scope":
                other = "project" if current_scope == "all" else "all"
                self.call_later(self.open_session_picker, other)
                return
            # Never await a new modal from inside the dismiss callback: Textual
            # runs it in the message pump and the modal would deadlock.
            self.run_worker(self._apply_session_action(action), exclusive=False)

        self.open_menu(
            "sessions",
            lambda: SessionPicker(
                self.sessions.entries(scope=current_scope), scope=current_scope
            ),
            selected,
        )

    async def _apply_session_action(self, action) -> None:
        from zenagent.agent.session import session_title

        if action.kind == "resume" and action.path is not None:
            from zenagent import project

            owner = project.project_key_for_session(action.path)
            current = project.project_key(project.find_project_root())
            await self.sessions.resume(action.path)
            self._reset_after_switch()
            if owner is not None and owner != current:
                self.notify("cross-project session: tools still run in this cwd")
            elif owner is None:
                self.notify("legacy session: tools run in this cwd")
            return
        if action.kind in ("new", "fork"):
            if action.kind == "new":
                await self.sessions.new()
            else:
                await self.sessions.fork()
            self._reset_after_switch()
            return
        target = action.path or self.sessions.current()
        if action.kind == "delete":
            if not await self._confirm(f"delete {target.name}? (moved to .trash)"):
                return
            was_current = target == self.sessions.current()
            self.sessions.delete(target)
            if was_current:
                self._reset_after_switch()
            self._refresh_status()
            self.notify("session moved to trash")
        elif action.kind == "export":
            exported = self.sessions.export(target)
            self.notify(f"exported: {exported}")
        elif action.kind == "rename":
            name = await self._prompt_rename(session_title(target))
            if name is None:
                return
            try:
                stored = self.sessions.rename(target, name)
            except ValueError as exc:
                self.notify(str(exc))
                return
            self.notify(f"renamed: {stored}")

    def _reset_after_switch(self) -> None:
        for log in self.query(TranscriptView):
            log.reset()
        self.chat.reload_transcript()
        self._refresh_status()

    async def _modal_result(self, screen):
        """Push a modal and await its dismissed value."""
        future: asyncio.Future = asyncio.get_running_loop().create_future()

        def done(result=None) -> None:
            if not future.done():
                future.set_result(result)

        self.push_screen(screen, done)
        return await future

    async def _confirm(self, prompt: str) -> bool:
        from zenagent.cli.tui.screens.modals import Confirm

        return bool(await self._modal_result(Confirm(prompt)))

    async def _prompt_rename(self, value: str) -> str | None:
        from zenagent.cli.tui.screens.modals import RenamePrompt

        return await self._modal_result(RenamePrompt(value))

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
        self.history.add(text)
        await self.chat.submit(text)

    async def _dispatch_command(self, name: str, argument: str) -> None:
        result = await self.commands.dispatch(name, argument)
        if result.screen == "model-picker":
            self.open_model_picker()
        elif result.screen == "session-picker":
            self.open_session_picker("project")
        elif result.screen == "session-picker-all":
            self.open_session_picker("all")
        elif result.screen == "mode-picker":
            self.open_mode_picker()
        elif result.screen == "thinking-picker":
            self.open_thinking_picker()
        elif result.screen == "settings":
            self.open_settings()
        elif result.screen == "search-settings":
            self.open_search_provider()
        elif result.show_help:
            self._show_help()
        elif result.reset_transcript:
            for log in self.query(TranscriptView):
                log.reset()
            self._refresh_status()
        elif result.quit:
            self.action_quit_app()
        elif result.message:
            for log in self.query(TranscriptView):
                log.append_notice(result.message)
        # `/mode` and friends change state the band renders; refresh after every
        # dispatch so the change is visible without waiting for the next event.
        self._refresh_status()

    def action_interrupt(self) -> None:
        """Esc: abort a run; on an empty idle draft, a double press rewinds.

        omp's chain: ``Esc`` always protects the draft, and two quick presses
        with nothing in the composer mean "undo the last turn". Anything else
        (a slower second press, a non-empty draft) is a no-op.
        """
        if self.chat.status.running:
            self._last_escape_at = None
            self.chat.abort()
            self.notify("interrupted", timeout=2)
            return
        prompt = self.query_one(PromptInput)
        if prompt.text:
            self._last_escape_at = None
            return
        now = time.monotonic()
        if (
            self._last_escape_at is not None
            and now - self._last_escape_at <= DOUBLE_ESCAPE_SECONDS
        ):
            self._last_escape_at = None
            self.run_worker(self._undo_last_turn())
        else:
            self._last_escape_at = now

    async def _undo_last_turn(self) -> None:
        """Double-Esc rewind: same path as ``/undo`` (new session file, recoverable)."""
        await self._dispatch_command("undo", "")

    def action_dequeue(self) -> None:
        """Alt+Up: take the last queued follow-up back into the prompt."""
        text = self.chat.dequeue_follow_up()
        if text is None:
            return
        prompt = self.query_one(PromptInput)
        prompt.insert(text)
        prompt.focus()

    def action_history_search(self) -> None:
        """Ctrl+R: search submitted prompts and recall one into the composer."""
        entries = self.history.entries()
        if not entries:
            self.notify("no prompt history yet", timeout=2)
            return

        from zenagent.cli.tui.screens.modals import HistorySearch

        async def selected(text: str | None) -> None:
            if not text:
                return
            prompt = self.query_one(PromptInput)
            prompt.clear()
            prompt.insert(text)
            prompt.focus()

        self.open_menu(
            "history", lambda: HistorySearch(list(reversed(entries))), selected
        )

    def action_clear_draft(self) -> None:
        """ctrl+c clears the draft (omp keeps a draft through Esc)."""
        prompt = self.query_one(PromptInput)
        if prompt.text:
            prompt.clear()

    def action_follow_up(self) -> None:
        """ctrl+q: queue while running, otherwise submit normally."""
        prompt = self.query_one(PromptInput)
        text = prompt.text.strip()
        if not text:
            return
        prompt.clear()
        if self.chat.status.running:
            self.chat.queue_follow_up(text)
        else:
            self.run_worker(self.chat.submit(text))

    def action_cycle_model(self) -> None:
        entries = [entry for entry in self.models.entries() if entry.available]
        if not entries:
            return
        ids = [entry.id for entry in entries]
        current = self.chat.status.model_id
        try:
            index = (ids.index(current) + 1) % len(ids)
        except ValueError:
            index = 0
        result = self.models.switch(ids[index])
        if not result.ok:
            self.notify(result.reason or "switch failed", timeout=3)
        self._refresh_status()

    def action_cycle_thinking(self) -> None:
        levels = ["off", "low", "medium", "high"]
        agent = self.runtime.agent
        current = agent.thinking or "off"
        try:
            agent.thinking = levels[(levels.index(current) + 1) % len(levels)]
        except ValueError:
            agent.thinking = "medium"
        self._refresh_status()

    def action_toggle_thinking(self) -> None:
        """ctrl+t expands/collapses every thinking row (omp toggles the block)."""
        from zenagent.cli.tui.widgets.rows import ThinkingRow

        rows = list(self.query(ThinkingRow))
        if not rows:
            return
        expand = any(row.collapsed for row in rows)
        for row in rows:
            row.set_expanded(expand)

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
