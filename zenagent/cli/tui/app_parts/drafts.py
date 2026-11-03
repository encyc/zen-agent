"""Draft editing, history recall and the submit router for typed input."""

from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
from pathlib import Path

from zenagent.app.skill_commands import build_skill_submission
from zenagent.cli.tui.widgets.prompt_input import PromptInput
from zenagent.cli.tui.widgets.transcript import TranscriptView
from zenagent.i18n import t
from zenagent.templates import expand


class DraftsMixin:
    """Composer actions. History state (``self.history``) stays on the App."""

    def remove_attachment(self, index: int) -> None:
        """Click on a chip: drop that pending image."""
        self.chat.remove_attachment(index)
        self._refresh_status()

    async def on_prompt_input_submitted(self, message: PromptInput.Submitted) -> None:
        text = message.text
        templates = {
            template.name: template for template in (self.runtime.templates or [])
        }
        extension_commands = self.runtime.extension_commands or {}
        # A /skill: token can appear at line start or inside a sentence; the
        # parser rejects drafts owned by another slash command.
        if "/skill:" in text:
            resolution = build_skill_submission(
                text,
                list(self.runtime.skills or []),
                reserved_names={*templates, *extension_commands},
            )
            if resolution is not None:
                if resolution.error is not None:
                    for log in self.query(TranscriptView):
                        log.append_notice(resolution.error)
                    return
                submission = resolution.submission
                if submission is not None:
                    # Keep the user's own `/skill:...` line in history; the
                    # injected body goes to the model, the chip to the transcript.
                    self.history.add(text)
                    await self.chat.submit(submission.prompt)
                    return
        if text.startswith("/"):
            name, _, argument = text[1:].partition(" ")
            if self.commands.get(name) is not None:
                await self._dispatch_command(name, argument)
                return
            template = templates.get(name)
            if template is not None:
                # Prompt templates are the REPL's `/name args` feature; the TUI
                # shares the same expansion so a saved command works in both.
                self.history.add(text)
                await self.chat.submit(expand(template.body, argument))
                return
            extension = extension_commands.get(name)
            if extension is not None:
                self.history.add(text)
                await self._run_extension_command(extension, argument)
                return
            for log in self.query(TranscriptView):
                log.append_notice(t("tui.app.unknown_command", name=name))
            return
        self.history.add(text)
        await self.chat.submit(text)

    def action_clear_draft(self) -> None:
        """ctrl+c clears the draft (omp keeps a draft through Esc)."""
        prompt = self.query_one(PromptInput)
        if prompt.text:
            prompt.clear()

    def action_history_search(self) -> None:
        """Ctrl+R: search submitted prompts and recall one into the composer."""
        entries = self.history.entries()
        if not entries:
            self.notify(t("tui.app.no_history"), timeout=2)
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

    def action_edit_external(self) -> None:
        """ctrl+g: edit the draft in ``$VISUAL``/``$EDITOR`` and put it back."""
        prompt = self.query_one(PromptInput)
        edited = self.edit_draft_externally(prompt.text)
        if edited is None:
            return
        prompt.clear()
        prompt.insert(edited)

    def action_paste_clipboard(self) -> None:
        """ctrl+v: attach an image path from the clipboard, else paste the text.

        Terminals do not hand image bytes to the app, so the pragmatic contract
        is "copy a path, press ctrl+v": an existing image file is attached
        through the same channel as ``/image``; anything else is inserted into
        the draft as text.
        """
        from zenagent.attachments import read_image_argument
        from zenagent.cli.tui.clipboard import read_clipboard_text

        text = read_clipboard_text()
        if not text:
            self.notify(t("tui.app.clipboard_empty"), timeout=2)
            return
        candidate = text.strip()
        if candidate and "\n" not in candidate:
            path = Path(candidate).expanduser()
            if path.is_file():
                try:
                    block = read_image_argument(path)
                except ValueError:
                    block = None
                if block is not None:
                    model = self.runtime.agent.model
                    if not model.supports_vision:
                        self.notify(
                            t("cmdmsg.image_not_supported", model=model.id),
                            timeout=3,
                        )
                        return
                    self.chat.attach_image(block)
                    self.notify(
                        t("tui.app.image_attached", mime=block.mime_type), timeout=2
                    )
                    return
        self.query_one(PromptInput).insert(text)

    def edit_draft_externally(self, text: str) -> str | None:
        """Open ``text`` in the user's editor; return the result or ``None``.

        Returns ``None`` (leaving the draft untouched) when no editor is
        configured, the editor exits non-zero, or the process cannot start.
        Split out from the action so tests can drive it without a terminal.
        """
        raw = os.environ.get("VISUAL") or os.environ.get("EDITOR")
        if not raw or not raw.strip():
            self.notify(t("tui.app.no_editor"), timeout=3)
            return None
        try:
            command = shlex.split(raw)
        except ValueError:
            command = []
        if not command:
            self.notify(t("tui.app.no_editor"), timeout=3)
            return None
        with tempfile.NamedTemporaryFile(
            "w", suffix=".md", delete=False, encoding="utf-8"
        ) as handle:
            handle.write(text)
            name = handle.name
        path = Path(name)
        try:
            try:
                with self.suspend():
                    code = subprocess.call([*command, str(path)])
            except Exception as exc:  # noqa: BLE001 - never kill the app
                self.notify(t("tui.app.editor_failed", error=exc), timeout=3)
                return None
            if code != 0:
                self.notify(t("tui.app.editor_failed", error=f"exit {code}"), timeout=3)
                return None
            return path.read_text(encoding="utf-8")
        finally:
            path.unlink(missing_ok=True)

