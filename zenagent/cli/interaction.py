"""The REPL's line-input plumbing (moved from repl.py, audit P2 Task 29).

split_input and the prompt_toolkit reader live here so the interactive loop
and the completion wiring are separate concerns; the ask/confirm interaction
closures stay in repl() for now (see the plan ledger).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

from zenagent.cli.completion import ReplCompleter
from zenagent.cli.keybindings import build_key_bindings

#: The REPL's line reader: an async zero-arg callable returning the next line.
InputFn = Callable[[], Awaitable[str]]


def split_input(line: str) -> tuple[str, str]:
    """``("", text)`` for a prompt, or ``(command, argument)`` for ``/command``."""
    stripped = line.strip()
    if stripped.startswith("/"):
        command, _, argument = stripped[1:].partition(" ")
        return command.lower(), argument.strip()
    return "", stripped




def _make_default_reader(
    completion: bool = True,
    extra_commands: dict[str, str] | None = None,
    keybindings: dict[str, str] | None = None,
    extra_values: dict[str, list[tuple[str, str]]] | None = None,
    *,
    typed_provider: Callable[[], list[tuple[str, str, str]]] | None = None,
    skill_provider: Callable[[], list[tuple[str, str]]] | None = None,
) -> InputFn:
    """prompt_toolkit reader with persistent history and completion."""
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import FileHistory

    history = Path.home() / ".zenagent" / "history"
    history.parent.mkdir(parents=True, exist_ok=True)
    commands = dict(extra_commands or {})
    session = PromptSession(
        history=FileHistory(str(history)),
        completer=(
            ReplCompleter(
                slash_commands=commands,
                extra_values=extra_values,
                typed_provider=typed_provider,
                skill_provider=skill_provider,
            )
            if completion
            else None
        ),
        key_bindings=build_key_bindings(keybindings),
    )

    async def read() -> str:
        return await asyncio.to_thread(session.prompt, "zenagent> ")

    return read

