"""The reusable dialog primitive: DialogScreen/DialogChoice/preview_diff (moved from modals.py, audit P2)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Generic, TypeVar

from rich.text import Text
from textual.screen import ModalScreen
from textual.widgets import Static

from zenagent.cli.tui.textutil import clip_cells
from zenagent.i18n import t
from zenagent.tools.fs import unified_diff

_T = TypeVar("_T")


class DialogScreen(ModalScreen[_T], Generic[_T]):
    """A centered, framed modal dialog.

    A bare ``ModalScreen`` only dims the screen underneath; the approval,
    confirmation and rename flows also need a panel, spacing, and the same
    title/detail/choice rhythm, so that chrome lives here once.
    """

    DEFAULT_CSS = """
    DialogScreen {
        align: center middle;
        background: $ink 65%;
    }

    DialogScreen #dialog {
        width: 90%;
        max-width: 88;
        height: auto;
        padding: 1 2;
        background: $surface;
        border: round $line;
    }

    DialogScreen #dialog-title {
        height: auto;
        padding: 0 0 1 0;
        color: $accent;
        text-style: bold;
    }

    DialogScreen #dialog-body {
        height: auto;
        margin: 0 0 1 0;
    }

    DialogScreen .dialog-detail {
        height: auto;
        color: $muted;
    }

    DialogScreen .dialog-command,
    DialogScreen .dialog-target {
        height: auto;
        color: $foreground;
    }

    DialogScreen .dialog-note {
        height: auto;
        color: $dim;
    }

    DialogScreen .dialog-add {
        height: auto;
        color: $success;
    }

    DialogScreen .dialog-remove {
        height: auto;
        color: $error;
    }

    DialogScreen #dialog-risk {
        height: auto;
        padding: 0 0 1 0;
        color: $warning;
        text-style: bold;
    }

    DialogScreen #dialog-diff {
        height: auto;
        margin: 0 0 1 0;
    }

    DialogScreen #dialog-choices {
        height: auto;
    }

    DialogScreen .dialog-choice {
        height: 1;
        color: $muted;
    }

    DialogScreen .dialog-choice:hover {
        background: $raised;
        color: $foreground;
    }

    DialogScreen .dialog-choice.-primary {
        color: $accent;
    }

    DialogScreen #dialog-hint {
        height: auto;
        margin: 1 0 0 0;
        color: $dim;
    }

    DialogScreen #rename-input {
        height: 1;
        padding: 0;
        border: none;
        background: $ink;
        color: $foreground;
    }
    """


class DialogChoice(Static):
    """One clickable option row; the screen's ``action_<name>`` handles it.

    ``on_select`` is for dynamic option lists (the ask dialog): when provided it
    runs on click instead of looking up ``action_<action>`` by name, so a screen
    can build rows from model-provided labels without one action method per row.
    """

    def __init__(
        self,
        text: Text,
        action: str,
        *,
        primary: bool = False,
        on_select: Callable[[], None] | None = None,
    ) -> None:
        self.action_name = action
        self._text = text
        self._on_select = on_select
        classes = "dialog-choice" + (" -primary" if primary else "")
        super().__init__(text, classes=classes)

    @property
    def plain(self) -> str:
        return self._text.plain

    def set_text(self, text: Text) -> None:
        """Replace the row label (multi-select toggles redraw their marker)."""
        self._text = text
        self.update(text)

    def on_click(self) -> None:
        if self._on_select is not None:
            self._on_select()
            return
        handler = getattr(self.screen, f"action_{self.action_name}", None)
        if handler is not None:
            handler()


def _choice(key: str, label: str) -> Text:
    """``[y] allow`` -- Text, so the square brackets stay literal markup."""
    return Text(t("tui.dialog.key_hint", key=key, label=label))


def _clip(text: str, limit: int) -> str:
    flat = " ".join(text.split())
    return clip_cells(flat, limit)


#: Above this size a diff preview is noise (and reading it would stall the UI).
_MAX_PREVIEW_BYTES = 256 * 1024


def preview_diff(
    tool_name: str, arguments: dict, *, max_lines: int = 12
) -> list[str]:
    """Bounded unified-diff preview for a write/edit approval, or ``[]``.

    Reads the target itself (the tool has not run yet); a missing or binary
    file, a non-matching edit, or any other tool yields no preview rather than
    a misleading one.
    """
    raw_path = arguments.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        return []
    target = Path(raw_path).expanduser()
    try:
        if not target.is_file():
            return []
        if target.stat().st_size > _MAX_PREVIEW_BYTES:
            return []
        data = target.read_bytes()
    except OSError:
        return []
    if b"\x00" in data[:8192]:
        return []
    before = data.decode("utf-8", errors="replace")
    if tool_name == "write_file":
        content = arguments.get("content")
        if not isinstance(content, str):
            return []
        after = content
    elif tool_name == "edit_file":
        old = arguments.get("old_string")
        new = arguments.get("new_string")
        if not isinstance(old, str) or not isinstance(new, str) or not old:
            return []
        if old not in before:
            return []
        after = before.replace(old, new, 1)
    else:
        return []
    lines = unified_diff(str(target), before, after).splitlines()
    if not lines:
        return []
    shown = lines[:max_lines]
    if len(lines) > max_lines:
        shown.append("…")
    return shown


