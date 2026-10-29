"""REPL completion: slash commands at line start, ``@path`` anywhere after space.

Kept separate from ``repl.py`` so it is testable without a terminal, and built
on a single command table that ``/help`` also renders — one source of truth.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from pathlib import Path

from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document

__all__ = ["SLASH_COMMANDS", "ReplCompleter", "help_text"]

#: Command → one-line description (drives both completion and ``/help``).
SLASH_COMMANDS: dict[str, str] = {
    "/help": "show this help",
    "/exit": "leave the REPL (Ctrl+D also works)",
    "/clear": "start over with an empty transcript",
    "/model": "show or switch the model (takes effect next turn)",
    "/thinking": "show or set reasoning level: off|low|medium|high|max",
    "/session": "show model, context and spend for this session",
    "/compact": "summarize the conversation into a fresh session",
    "/undo": "drop the last turn into a fresh session file",
    "/image": "attach an image for the next prompt (/image <path>)",
    "/images": "list attached images, or /images clear",
    "/mcp": "show MCP servers, or /mcp retry to reconnect",
    "/skills": "list available skills",
    "/reload": "re-discover skills, templates and extensions",
    "/sessions": "list saved sessions",
    "/fork": "branch a new session at a user message (/fork <n>)",
    "/import": "load a session file and continue it (/import <path>)",
}

#: Text matched when the cursor sits in an ``@path`` token.
_AT_TOKEN = re.compile(r"(?:^|\s)@(\S*)$")


def help_text() -> str:
    """Render the command table plus usage notes for ``/help``."""
    width = max(len(name) for name in SLASH_COMMANDS)
    rows = [f"  {name:<{width}}  {description}" for name, description in SLASH_COMMANDS.items()]
    return (
        "commands:\n"
        + "\n".join(rows)
        + "\nwhile the agent is working, any line you type is queued as steering input."
    )


class ReplCompleter(Completer):
    """Completes slash commands and ``@`` file paths."""

    def __init__(
        self,
        slash_commands: dict[str, str] | None = None,
        base_dir: Path | None = None,
    ) -> None:
        self.slash_commands = dict(slash_commands or SLASH_COMMANDS)
        self.base_dir = Path(base_dir) if base_dir is not None else Path.cwd()

    def get_completions(
        self, document: Document, complete_event
    ) -> Iterable[Completion]:
        text = document.text_before_cursor

        if text.startswith("/") and " " not in text:
            for name, description in sorted(self.slash_commands.items()):
                if name.startswith(text):
                    yield Completion(
                        name,
                        start_position=-len(text),
                        display=name,
                        display_meta=description,
                    )
            return

        match = _AT_TOKEN.search(text)
        if match is not None:
            yield from self._path_completions("@" + match.group(1))

    def _path_completions(self, token: str) -> Iterable[Completion]:
        raw = token[1:]
        absolute = raw.startswith("/")
        base = Path(raw) if absolute else self.base_dir / raw
        if raw.endswith("/") or raw == "":
            directory, prefix = base, ""
        else:
            directory, prefix = base.parent, base.name

        try:
            entries = sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name))
        except OSError:
            return
        for entry in entries:
            if entry.name.startswith("."):
                continue  # keep hidden files out unless explicitly typed
            if not entry.name.startswith(prefix):
                continue
            if absolute:
                completed = str(entry)
            else:
                completed = os.path.relpath(entry, self.base_dir)
                completed = "" if completed == "." else completed
            suffix = "/" if entry.is_dir() else ""
            yield Completion(
                "@" + completed + suffix,
                start_position=-len(token),
                display=entry.name + suffix,
                display_meta="dir" if entry.is_dir() else "",
            )
