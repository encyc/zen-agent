"""Main screen layout: transcript area, status bar, prompt input."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.screen import Screen

from zenagent.cli.tui.widgets.chat_log import ChatLog
from zenagent.cli.tui.widgets.prompt_input import PromptInput
from zenagent.cli.tui.widgets.status_bar import StatusBar

__all__ = ["MainScreen"]


class MainScreen(Screen):
    """The single chat screen."""

    def compose(self) -> ComposeResult:
        yield ChatLog()
        yield StatusBar()
        yield PromptInput()
