"""Draft hint bar: accept/dismiss/move actions over the shared matching logic."""

from __future__ import annotations

from zenagent.cli.completion import (
    argument_matches,
    command_matches,
    hint_context,
    skill_matches,
)
from zenagent.cli.tui.widgets.prompt_input import PromptInput


class HintsMixin:
    """Hint-bar actions. Draft state (``_hint_*`` fields) stays on the App."""

    def _hint_context(
        self, text: str, cursor: int
    ) -> tuple[list[tuple[str, str]], str, int, int, str]:
        return hint_context(self.commands, self.runtime.skills, self.models, text, cursor)

    def _command_matches(self, token_text: str) -> list[tuple[str, str]]:
        return command_matches(self.commands, token_text)

    def _skill_matches(self, token_text: str) -> list[tuple[str, str]]:
        return skill_matches(self.runtime.skills, token_text)

    def _argument_values(self, name: str) -> list[tuple[str, str]]:
        return argument_matches(self.models, self.commands, name, "")

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
        draft = prompt.text
        if self._hint_kind == "arg":
            # Argument values replace the typed value and keep the old shape.
            replacement = f"{selected} "
            tail = ""
        else:
            # Slash entries may be completed mid-sentence: replace only the
            # token, keep the prose before and after it.
            replacement = f"/{selected} "
            tail = draft[self._hint_end :]
        new_text = draft[: self._hint_start] + replacement + tail
        prompt.clear()
        prompt.insert(new_text)
        prompt.move_cursor(
            prompt.document.get_location_from_index(
                self._hint_start + len(replacement)
            )
        )
