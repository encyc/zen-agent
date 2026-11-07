"""REPL completion: slash commands at line start, ``@path`` anywhere after space.

Kept separate from ``repl.py`` so it is testable without a terminal. The slash
table is derived from the live command registry — the same source ``/help``
and the TUI hints read — so there is one source of truth for command names.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterable
from pathlib import Path

from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document

from zenagent.app.skill_commands import SKILL_SLASH_PREFIX, slash_token_at
from zenagent.app.slash_args import argument_values
from zenagent.i18n import t

__all__ = [
    "ReplCompleter",
    "command_matches",
    "help_text",
    "hint_context",
    "skill_matches",
    "slash_commands",
]


def slash_commands(registry) -> dict[str, str]:
    """The live command table with descriptions in the active locale.

    Names come from the registry (single source of truth). Descriptions
    re-resolve the ``cmd.<name>.description`` key at read time so ``/lang``
    switches the table live; commands without a catalog key (extensions)
    fall back to their registered description.
    """
    table: dict[str, str] = {}
    for command in registry.names():
        key = f"cmd.{command.name}.description"
        translated = t(key)
        table[f"/{command.name}"] = (
            command.description if translated == key else translated
        )
    return table

#: Text matched when the cursor sits in an ``@path`` token.
_AT_TOKEN = re.compile(r"(?:^|\s)@(\S*)$")


def help_text(registry) -> str:
    """Render the command table plus usage notes for ``/help``."""
    table = slash_commands(registry)
    width = max(len(name) for name in table)
    rows = [f"  {name:<{width}}  {description}" for name, description in table.items()]
    return (
        t("repl.help.header")
        + "\n"
        + "\n".join(rows)
        + "\n"
        + t("repl.help.footer")
    )


# -- TUI hint matching -------------------------------------------------------
#
# The same matching the draft's hint bar uses, as pure functions so the REPL
# completer and the TUI hints cannot drift apart.


def command_matches(commands, token_text: str) -> list[tuple[str, str]]:
    """Registered slash commands whose name starts with the typed prefix."""
    needle = token_text[1:].lower()
    return [
        (
            command.name,
            f"{t('type.command')} · {command.description}",
        )
        for command in commands.names()
        if command.name.startswith(needle)
    ]


def skill_matches(skills, token_text: str) -> list[tuple[str, str]]:
    """``/skill:<name>`` entries matching a token, bare-prefix with fallback."""
    skills = [skill for skill in (skills or [])]
    if not skills:
        return []
    if token_text.startswith("/skill:"):
        needle = token_text[len("/skill:") :].lower()
        selected = [
            skill
            for skill in skills
            if skill.name.lower().startswith(needle)
        ]
    else:
        needle = token_text[1:].lower()
        selected = [
            skill for skill in skills if skill.name.lower().startswith(needle)
        ]
        if not selected:
            selected = [
                skill
                for skill in skills
                if f"skill:{skill.name}".lower().startswith(needle)
            ]
    return [
        (
            f"skill:{skill.name}",
            f"{t('type.skill')} · {skill.description}",
        )
        for skill in sorted(selected, key=lambda item: item.name)
    ]


def argument_matches(models, commands, name: str, needle: str) -> list[tuple[str, str]]:
    """A command's argument values; model ids come from the catalog.

    ``needle`` (lowercase) keeps only values with that prefix, so an Enter on
    the highlighted value stays a no-op instead of switching to a model whose
    key is missing.
    """
    if name == "model":
        # Enter executes the highlighted value, so the list leads with the
        # current model and then the usable ones.
        entries = sorted(
            models.entries(),
            key=lambda entry: (not entry.current, not entry.available),
        )
        values = [
            (
                entry.id,
                f"{entry.provider} · "
                + (
                    t("tui.app.model_ready")
                    if entry.available
                    else entry.reason or t("tui.app.model_unavailable")
                ),
            )
            for entry in entries
        ]
    else:
        values = argument_values("/" + name)
    return [
        (value, description)
        for value, description in values
        if value.lower().startswith(needle)
    ]


def hint_context(
    commands, skills, models, text: str, cursor: int
) -> tuple[list[tuple[str, str]], str, int, int, str]:
    """``(matches, prefix, replace start, replace end, kind)`` for the draft.

    Line-start ``/`` lists commands and skill commands; an embedded ``/``
    token (mid-sentence) lists only ``/skill:<name>`` entries; after a known
    command's space we list that command's argument values.
    """
    if "\n" in text:
        return [], "/", 0, 0, "slash"
    token = slash_token_at(text, cursor)
    if token is not None:
        line_prefix = text[token.line_start : token.start].strip()
        if not line_prefix:
            if token.text.startswith("/skill:"):
                matches = skill_matches(skills, token.text)
            else:
                matches = [
                    *command_matches(commands, token.text),
                    *skill_matches(skills, token.text),
                ]
                matches.sort(key=lambda item: item[0])
            return matches, "/", token.start, token.end, "slash"
        line = text[token.line_start :].lstrip()
        if line.startswith("/") and not line.startswith("/skill:"):
            return [], "/", 0, 0, "slash"
        return (
            skill_matches(skills, token.text),
            "/",
            token.start,
            token.end,
            "slash",
        )

    if cursor == len(text) and text.startswith("/") and " " in text:
        head, _, rest = text.partition(" ")
        name = head[1:].lower()
        if " " not in rest and commands.get(name) is not None:
            needle = rest.strip().lower()
            matches = argument_matches(models, commands, name, needle)
            return matches, "", len(head) + 1, len(text), "arg"
    return [], "/", 0, 0, "slash"


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
        if slash_commands is None:
            self.slash_commands = {}
        else:
            self.slash_commands = dict(slash_commands)
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
                (
                    name,
                    f"{t('type.skill')} · {skills[name][0]}",
                )
                for name in sorted(selected)
            ]
        else:
            matches = [
                (name, f"{t(f'type.{kind}')} · {description}")
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
