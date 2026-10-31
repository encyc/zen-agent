"""Banner above the transcript while a read-only subagent view is open.

The view is a swap, not a new screen: the parent transcript stays mounted and
synced behind the swap, so this one row is what tells the user where they are
and how to get back.
"""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

from zenagent.cli.tui import palette
from zenagent.i18n import t

__all__ = ["SubagentBanner"]


def _status_label(status: str) -> str:
    return t("tui.subagent.done") if status == "ok" else status


class SubagentBanner(Static):
    """``◐ subagent: research · 只读 · esc 返回``; hidden outside the view."""

    DEFAULT_CSS = """
    SubagentBanner {
        height: 1;
        background: $surface;
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
        text.append("◐ " if running else "✓ ", style=palette.ACCENT if running else palette.OK)
        text.append(t("tui.subagent.label"), style=palette.ACCENT)
        text.append(name, style=palette.MUTED)
        if not running and status:
            label = _status_label(status)
            text.append(f" · {label}", style=palette.MUTED)
        text.append(" · " + t("tui.subagent.readonly"), style=palette.DIM)
        self._plain = text.plain
        self.update(text)
        self.display = True

    def hide(self) -> None:
        self._plain = ""
        self.update("")
        self.display = False
