"""The transcript row for an injected ``/skill:<name>`` user message.

The message sent to the model carries the whole SKILL.md body; rendering that
verbatim would bury the conversation. This widget shows a one-line chip
(``✦ skill:<name> · args``) while collapsed and the injected body when expanded
(like a tool card). Enter/space/click toggles it.
"""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

from zenagent.app.views import MessageView
from zenagent.cli.tui import palette

__all__ = ["SkillBlock"]

_TEXT = palette.TEXT
_MUTED = palette.MUTED
_DIM = palette.DIM
_ACCENT = palette.ACCENT

#: Longest arg preview shown in the collapsed chip.
_MAX_ARGS = 98


class SkillBlock(Static):
    """A compact, expandable skill invocation row."""

    can_focus = True
    BINDINGS = [("enter,space", "toggle", "Expand")]  # noqa: RUF012 - Textual API

    def __init__(self, view: MessageView) -> None:
        self.skill_name = view.text
        self.skill_args = view.skill_args or ""
        self.skill_body = view.skill_body or ""
        self.collapsed = True
        super().__init__(self._renderable(), classes="skill-message")

    @property
    def plain(self) -> str:
        return self._renderable().plain

    def _width(self) -> int:
        try:
            width = self.size.width
        except RuntimeError:  # not mounted yet
            width = 0
        return max(24, (width or 80) - 2)

    def _renderable(self) -> Text:
        text = Text()
        text.append("✦ ", style=f"bold {_ACCENT}")
        text.append("skill:", style=f"bold {_ACCENT}")
        text.append(self.skill_name, style=f"bold {_TEXT}")
        args = " ".join(self.skill_args.split())
        if args:
            clipped = args if len(args) <= _MAX_ARGS else args[: _MAX_ARGS - 1] + "…"
            text.append(" · ", style=_DIM)
            text.append(clipped, style=_MUTED)
        if not self.collapsed:
            text.append("\n")
            for line in self.skill_body.splitlines():
                text.append(line, style=_MUTED)
                text.append("\n")
        return text

    def _sync_classes(self) -> None:
        if not self.is_mounted:
            return
        if self.collapsed:
            self.remove_class("expanded")
        else:
            self.add_class("expanded")

    def on_mount(self) -> None:
        self._sync_classes()
        self.update(self._renderable())
        self.call_after_refresh(lambda: self.update(self._renderable()))

    def set_expanded(self, expanded: bool) -> None:
        if expanded == (not self.collapsed):
            return
        self.collapsed = not expanded
        self._sync_classes()
        if self.is_mounted:
            self.update(self._renderable())

    def action_toggle(self) -> None:
        self.collapsed = not self.collapsed
        self._sync_classes()
        self.update(self._renderable())

    def on_click(self) -> None:
        """Click the row to expand/collapse it, like the tool cards."""
        self.action_toggle()
