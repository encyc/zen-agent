"""The opening block that scrolls away with the first turn.

omp calls this the "header": a centered wordmark, the version and one hint
line. It is not a panel -- it lives in the transcript flow and is retired as
soon as real output pushes it off screen.
"""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

from zenagent import __version__
from zenagent.cli.tui import palette

__all__ = ["WelcomeBlock"]

_TEXT = palette.TEXT
_MUTED = palette.MUTED
_DIM = palette.DIM
_ACCENT = palette.ACCENT


class WelcomeBlock(Static):
    """`zen` wordmark, version and a single quiet hint line."""

    def __init__(self) -> None:
        super().__init__(self._renderable(), classes="welcome")

    def _renderable(self) -> Text:
        text = Text()
        text.append("\n")
        text.append("zen\n", style=f"bold {_ACCENT}")
        text.append(f"v{__version__} · python agent loop\n", style=_DIM)
        text.append("\n")
        text.append("/ commands  ·  ctrl+b info  ·  ctrl+d quit\n", style=_MUTED)
        text.append("\n")
        return text

    @property
    def plain(self) -> str:
        return self._renderable().plain
