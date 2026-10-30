"""The `╰─ ` cue that anchors the prompt, omp's default ``band`` composer shape.

Only the first row draws the elbow; the rest are blank so multi-line drafts stay
aligned under the text (the input carries a 3-column left padding of its own).
"""

from __future__ import annotations

from rich.segment import Segment
from rich.style import Style
from textual.strip import Strip
from textual.widget import Widget

__all__ = ["PromptGutter"]

_ACCENT = Style(color="#d9a86c")
_BLANK = Style(color="#d7e0e8")
_INDENT = "   "


class PromptGutter(Widget):
    """Three columns wide; row 0 is ``╰─ ``, every other row is blank."""

    DEFAULT_CSS = """
    PromptGutter {
        width: 3;
        height: 1;
    }
    """

    def __init__(self) -> None:
        super().__init__(id="prompt-gutter")

    @property
    def plain(self) -> str:
        return "╰─"

    def render_line(self, y: int) -> Strip:
        if y == 0:
            return Strip([Segment("╰─ ", _ACCENT)])
        return Strip([Segment(_INDENT, _BLANK)])
