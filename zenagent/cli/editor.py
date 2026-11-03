"""``$VISUAL``/``$EDITOR`` handoff for line-based frontends (the REPL).

The TUI has its own suspend-based editor (``app.py``) because a fullscreen
app must suspend first; the REPL has no screen to suspend, so a plain
subprocess between prompts is enough. Split out so tests can drive it
without a terminal.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
from pathlib import Path

__all__ = ["run_editor"]


def run_editor(initial: str) -> str | None:
    """Open ``initial`` in the user's editor; return the edited text or None."""
    raw = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if not raw or not raw.strip():
        return None
    try:
        command = shlex.split(raw)
    except ValueError:
        return None
    if not command:
        return None
    with tempfile.NamedTemporaryFile(
        "w", suffix=".md", delete=False, encoding="utf-8"
    ) as handle:
        handle.write(initial)
        name = handle.name
    path = Path(name)
    try:
        if subprocess.call([*command, str(path)]) != 0:
            return None
        return path.read_text(encoding="utf-8")
    finally:
        path.unlink(missing_ok=True)
