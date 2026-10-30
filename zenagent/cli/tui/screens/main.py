"""Main screen layout: transcript area, status bar, prompt input."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Static

from zenagent.cli.tui.widgets.prompt_input import PromptInput
from zenagent.cli.tui.widgets.status_bar import StatusBar

__all__ = ["MainScreen"]


class MainScreen(Screen):
    """The single chat screen."""

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="chat"):
            yield Static("ZenAgent — /help for commands, Enter to send", id="welcome")
        yield StatusBar()
        yield PromptInput()
