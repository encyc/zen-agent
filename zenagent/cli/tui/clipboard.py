"""Best-effort clipboard text reader for the TUI (stdlib only, no deps).

Ctrl+V is only useful if the app can ask the OS what the clipboard holds; every
supported platform ships a tiny tool for it (macOS ``pbpaste``, Wayland
``wl-paste``, X11 ``xclip``, Windows PowerShell). If none exists the action
degrades to a notice instead of failing.

The reader returns *text*. Reading image bytes from the clipboard would need a
platform-specific API or a new dependency; the composer instead accepts an
image path pasted as text and attaches it through the same channel as
``/image``.
"""

from __future__ import annotations

import shutil
import subprocess

__all__ = ["read_clipboard_text", "write_clipboard_text"]

#: ``(binary, argv)`` candidates, most native first.
_COMMANDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("pbpaste", ()),
    ("wl-paste", ("--no-newline",)),
    ("xclip", ("-selection", "clipboard", "-o")),
    ("xsel", ("--clipboard", "--output")),
    ("powershell", ("-NoProfile", "-Command", "Get-Clipboard")),
)

#: A clipboard read must never stall the UI thread.
_TIMEOUT_S = 2.0


def read_clipboard_text() -> str | None:
    """Return the clipboard's text, or ``None`` when no reader is available."""
    for name, arguments in _COMMANDS:
        executable = shutil.which(name)
        if executable is None:
            continue
        try:
            completed = subprocess.run(
                [executable, *arguments],
                capture_output=True,
                text=True,
                timeout=_TIMEOUT_S,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if completed.returncode == 0:
            return completed.stdout
    return None


#: ``(binary, argv)`` writers for :func:`write_clipboard_text`.
_WRITERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("pbcopy", ()),
    ("wl-copy", ()),
    ("xclip", ("-selection", "clipboard")),
    ("xsel", ("--clipboard", "--input")),
    ("powershell", ("-NoProfile", "-Command", "Set-Clipboard")),
)


def write_clipboard_text(text: str) -> bool:
    """Put ``text`` on the system clipboard; False when no writer exists."""
    for name, arguments in _WRITERS:
        executable = shutil.which(name)
        if executable is None:
            continue
        try:
            completed = subprocess.run(
                [executable, *arguments],
                input=text,
                text=True,
                timeout=_TIMEOUT_S,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if completed.returncode == 0:
            return True
    return False
