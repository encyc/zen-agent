"""Banner above the transcript while a read-only subagent view is open.

The view is a swap, not a new screen: the parent transcript stays mounted and
synced behind the swap, so this one row is what tells the user where they are
and how to get back.
"""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

__all__ = ["SubagentBanner"]

_ACCENT = "#d9a86c"
_MUTED = "#8296a6"
_DIM = "#5d6f7e"


class SubagentBanner(Static):
    """``◐ subagent: research · 只读 · esc 返回``; hidden outside the view."""

    DEFAULT_CSS = """
    SubagentBanner {
        height: 1;
        background: #141c24;
        padding: 0 2;
    }
    """

    def __init__(self) -> None:
        super().__init__("", id="subagent-banner")
        self._plain = ""
        self.display = False

    @property
    def plain(self) -> str:
        return self._plain

    def show(self, name: str, *, status: str | None = None) -> None:
        running = status in (None, "running")
        text = Text()
        text.append("◐ " if running else "✓ ", style=_ACCENT)
        text.append("subagent: ", style=_ACCENT)
        text.append(name, style=_MUTED)
        if not running and status:
            text.append(f" · {status}", style=_MUTED)
        text.append(" · 只读 · esc 返回", style=_DIM)
        self._plain = text.plain
        self.update(text)
        self.display = True

    def hide(self) -> None:
        self._plain = ""
        self.update("")
        self.display = False
