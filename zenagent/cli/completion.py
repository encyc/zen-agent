"""REPL completion: slash commands at line start, ``@path`` anywhere after space.

Kept separate from ``repl.py`` so it is testable without a terminal, and built
on a single command table that ``/help`` also renders — one source of truth.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterable
from pathlib import Path

from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document

from zenagent.app.skill_commands import SKILL_SLASH_PREFIX
from zenagent.app.slash_args import argument_values

__all__ = ["SLASH_COMMANDS", "ReplCompleter", "help_text"]

#: Command → one-line description (drives both completion and ``/help``).
SLASH_COMMANDS: dict[str, str] = {
    "/help": "show this help",
    "/exit": "leave the REPL (Ctrl+D also works)",
    "/clear": "start over with an empty transcript",
    "/model": "show or switch the model (takes effect next turn)",
    "/thinking": "show or set reasoning level: off|low|medium|high|max",
    "/mode": "set runtime mode: plan|ask|full (plan = read-only)",
    "/session": "show model, context and spend for this session",
    "/tools": "list the tools the model can call",
    "/compact": "summarize the conversation into a fresh session",
    "/undo": "drop the last turn into a fresh session file",
    "/retry": "re-run the last turn (new session file)",
    "/image": "attach an image for the next prompt (/image <path>)",
    "/images": "list attached images, or /images clear",
    "/mcp": "show MCP servers, or /mcp retry to reconnect",
    "/skills": "list available skills",
    "/reload": "re-discover skills, templates and extensions",
    "/theme": "switch theme: default|mono|solarized|<path.json>",
    "/hotkeys": "show the active keybindings",
    "/sessions": "list saved sessions (/sessions all for every project)",
    "/rename": "rename the current session (/rename <name>)",
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
    """Completes slash commands (line-start and embedded skill tokens) and ``@`` paths.

    Slash completion is token-based so ``please /skill:pd`` can complete in the
    middle of a sentence: a line-start ``/`` token offers commands, templates,
    extension commands and skills; an embedded ``/`` token offers only
    ``/skill:<name>`` entries (the only slash token meaningful mid-sentence).
    """

    def __init__(
        self,
        slash_commands: dict[str, str] | None = None,
        base_dir: Path | None = None,
        extra_values: dict[str, list[tuple[str, str]]] | None = None,
        *,
        typed_commands: list[tuple[str, str, str]] | None = None,
        typed_provider: Callable[[], list[tuple[str, str, str]]] | None = None,
        skill_provider: Callable[[], list[tuple[str, str]]] | None = None,
    ) -> None:
        self.slash_commands = dict(slash_commands or SLASH_COMMANDS)
        self.base_dir = Path(base_dir) if base_dir is not None else Path.cwd()
        #: Frontend-supplied value sets (e.g. model ids), keyed with the slash.
        self.extra_values = dict(extra_values or {})
        #: Extra slash entries as ``(name, description, kind)``; kind is shown
        #: in the completion meta (template/extension/...).
        self.typed_commands = list(typed_commands or [])
        #: Live extra entries; /reload rebuilds them without a new reader.
        self.typed_provider = typed_provider
        #: Live skill list ``[(name, description)]``; re-read on every keystroke
        #: so /reload's rebuilt list is reflected without recreating the reader.
        self.skill_provider = skill_provider

    # -- entries -----------------------------------------------------------

    def _slash_entries(self) -> dict[str, tuple[str, str]]:
        entries: dict[str, tuple[str, str]] = {
            name: (description, "command")
            for name, description in self.slash_commands.items()
        }
        typed = (
            self.typed_provider() if self.typed_provider is not None else self.typed_commands
        )
        for name, description, kind in typed:
            entries[name] = (description, kind)
        if self.skill_provider is not None:
            for name, description in self.skill_provider():
                entries.setdefault(
                    f"{SKILL_SLASH_PREFIX}{name}", (description, "skill")
                )
        return entries

    def _token(self, text: str) -> tuple[int, int, str, int] | None:
        """``(start, end, token, line_start)`` for the current ``/`` token."""
        end = len(text)
        start = end
        while start > 0 and not text[start - 1].isspace():
            start -= 1
        token = text[start:end]
        if not token.startswith("/"):
            return None
        line_start = text.rfind("\n", 0, start) + 1
        return start, end, token, line_start

    def _slash_completions(
        self, text: str
    ) -> tuple[list[tuple[str, str, str]], int] | None:
        token = self._token(text)
        if token is None:
            return None
        start, _end, token_text, line_start = token
        line_prefix = text[line_start:start].strip()
        skill_only = token_text.startswith(SKILL_SLASH_PREFIX)
        if line_prefix:
            # Mid-sentence: only skill tokens are meaningful. Suppress when the
            # line is already another slash command's argument list.
            line_head = text[line_start:].lstrip()
            if line_head.startswith("/") and not line_head.startswith(
                SKILL_SLASH_PREFIX
            ):
                return [], -len(token_text)
            skill_only = True

        entries = self._slash_entries()
        if skill_only:
            skills = {name: (description, kind) for name, (description, kind) in entries.items() if kind == "skill"}
            if token_text.startswith(SKILL_SLASH_PREFIX):
                # "/skill:re" filters by the skill name itself.
                needle = token_text[len(SKILL_SLASH_PREFIX) :].lower()
                selected = [
                    name
                    for name in skills
                    if name[len(SKILL_SLASH_PREFIX) :].lower().startswith(needle)
                ]
            else:
                # A bare "/p" filters by skill-name prefix; "/sk" (the start
                # of "skill:") falls back to the command prefix so the whole
                # namespace is offered once no name matches.
                needle = token_text[1:].lower()
                selected = [
                    name
                    for name in skills
                    if name[len(SKILL_SLASH_PREFIX) :].lower().startswith(needle)
                ]
                if not selected:
                    selected = [
                        name
                        for name in skills
                        if name[1:].lower().startswith(needle)
                    ]
            matches = [
                (name, f"{skills[name][1]} · {skills[name][0]}")
                for name in sorted(selected)
            ]
        else:
            matches = [
                (name, f"{kind} · {description}")
                for name, (description, kind) in entries.items()
                if name.lower().startswith(token_text.lower())
            ]
        return sorted(matches), -len(token_text)

    def get_completions(
        self, document: Document, complete_event
    ) -> Iterable[Completion]:
        text = document.text_before_cursor

        slash = self._slash_completions(text)
        if slash is not None:
            matches, start_position = slash
            for name, description in matches:
                yield Completion(
                    name,
                    start_position=start_position,
                    display=name,
                    display_meta=description,
                )
            return

        if text.startswith("/") and " " in text:
            head, _, rest = text.partition(" ")
            if " " not in rest:
                values = self.extra_values.get(head.lower())
                if values is None:
                    values = argument_values(head, rest)
                else:
                    values = [
                        (value, description)
                        for value, description in values
                        if value.lower().startswith(rest.strip().lower())
                    ]
                if values:
                    for value, description in values:
                        yield Completion(
                            value,
                            start_position=-len(rest),
                            display=value,
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
