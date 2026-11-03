"""Command dispatch: palette wiring, slash-command routing, extension bridge."""

from __future__ import annotations

import inspect
from typing import ClassVar

from zenagent.app.commands import AppServices, build_default_commands
from zenagent.cli.tui.widgets.transcript import TranscriptView
from zenagent.i18n import t


class CommandsMixin:
    """Dispatch and registration; ``SCREEN_OPENERS`` maps command screens.

    Pinned from both ends by ``tests/test_tui_screen_registry.py``: the keys
    must equal the ``screen=`` literals in ``zenagent/app/commands.py``.
    """

    #: ``CommandResult.screen`` → the App method that opens it. Pinned from
    #: both ends by ``tests/test_tui_screen_registry.py``: the keys must equal
    #: the ``screen=`` literals in ``zenagent/app/commands.py``.
    SCREEN_OPENERS: ClassVar[dict[str, str]] = {
        "model-picker": "open_model_picker",
        "session-picker": "open_session_picker",
        "session-picker-all": "open_session_picker",
        "mode-picker": "open_mode_picker",
        "thinking-picker": "open_thinking_picker",
        "theme-picker": "open_theme_picker",
        "language-picker": "open_language_picker",
        "settings": "open_settings",
        "plan-review": "open_plan_review",
        "display-reset": "action_display_reset",
        "hotkeys": "open_hotkeys",
        "usage-dashboard": "open_usage_dashboard",
        "mcp-add": "open_mcp_add",
        "todo-edit": "open_todo_editor",
        "search-settings": "open_search_provider",
        "memory-manager": "open_memory_manager",
        "memory-add": "open_memory_add",
        "memory-edit": "open_memory_edit",
    }

    #: Screens whose opener consumes ``result.target`` (the entry id to edit,
    #: or the title prefill for a new one).
    SCREEN_ARG_KEYS: ClassVar[frozenset[str]] = frozenset(
        {"memory-add", "memory-edit"}
    )

    def get_system_commands(self, screen):
        from textual.app import SystemCommand

        yield from super().get_system_commands(screen)
        yield SystemCommand(
            t("tui.app.show_todo"),
            t("tui.app.show_todo_desc"),
            self.action_show_todo_list,
        )
        for command in self.commands.names():
            yield SystemCommand(
                command.name,
                command.description,
                lambda name=command.name: self._run_command(name),
            )

    def _run_command(self, name: str) -> None:
        """Palette entries run the same path as typing the command."""
        self.run_worker(self._dispatch_command(name, ""))

    def _show_help(self) -> None:
        for log in self.query(TranscriptView):
            log.append_notice(t("tui.app.help_header"))
            for command in self.commands.names():
                log.append_notice(
                    f"  /{command.name}  {command.description}"
                )

    async def _dispatch_command(self, name: str, argument: str) -> None:
        try:
            result = await self.commands.dispatch(name, argument)
        except Exception as exc:  # noqa: BLE001 - a command must never exit the app
            # Slash commands run inside an input handler: anything escaping here
            # reaches Textual's _handle_exception, which tears the app down.
            for log in self.query(TranscriptView):
                log.append_notice(
                    t("tui.app.command_failed", name=name, error=exc)
                )
            return
        if result.model:
            # `/model <id>` (or Enter on a value hint) switches directly; only a
            # bare `/model` reaches the picker branch below.
            self._apply_model(result.model)
        opener = self.SCREEN_OPENERS.get(result.screen or "")
        if opener is not None:
            method = getattr(self, opener)
            # The two session-picker keys share one opener; the registry key
            # carries the scope a bare `/sessions` vs `/sessions all` means.
            if result.screen == "session-picker-all":
                method("all")
            elif result.screen == "session-picker":
                method("project")
            elif result.screen in self.SCREEN_ARG_KEYS:
                method(result.target)
            else:
                method()
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
        if result.appearance:
            applied = self.set_appearance(result.appearance)
            for log in self.query(TranscriptView):
                log.append_notice(t("tui.app.theme_set", value=applied))
        if result.language:
            code = self.set_language(result.language)
            for log in self.query(TranscriptView):
                log.append_notice(t("tui.app.language_set", value=code))
        # `/mode` and friends change state the band renders; refresh after every
        # dispatch so the change is visible without waiting for the next event.
        self._refresh_status()

    async def _run_extension_command(self, command, argument: str) -> None:
        """Run an extension's slash command; its return value becomes a notice."""
        try:
            result = command.handler(self.runtime.agent, argument)
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:  # noqa: BLE001 - an extension must not kill the app
            for log in self.query(TranscriptView):
                log.append_notice(
                    t("tui.app.command_failed", name=command.name, error=exc)
                )
            return
        if result:
            for log in self.query(TranscriptView):
                log.append_notice(str(result))

    def _rebuild_commands(self) -> None:
        """Describe commands in the active language (registry text is resolved)."""
        self.commands = build_default_commands(
            AppServices(
                runtime=self.runtime,
                chat=self.chat,
                models=self.models,
                sessions=self.sessions,
            )
        )

