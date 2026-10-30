"""Main screen layout: sidebar + transcript, status bar and prompt input."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.screen import Screen

from zenagent.cli.tui.widgets.chat_log import ChatLog
from zenagent.cli.tui.widgets.prompt_input import PromptInput
from zenagent.cli.tui.widgets.sidebar import Sidebar
from zenagent.cli.tui.widgets.status_bar import StatusBar

__all__ = ["MainScreen"]


class MainScreen(Screen):
    """The single chat screen."""

    def compose(self) -> ComposeResult:
        with Horizontal(id="body"):
            yield Sidebar()
            yield ChatLog()
        yield StatusBar()
        yield PromptInput()
