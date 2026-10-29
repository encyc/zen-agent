"""Extensions: user Python modules that add tools, commands and prompt text.

An extension is a module exposing ``register(api)``:

    def register(api):
        api.register_tool(my_tool)
        api.register_command("hello", "say hi", handler)
        api.add_system_prompt("This project uses tabs.")
        api.on("agent_end", lambda event: ...)

Extensions are opt-in (config or ``--extension``) and isolated: a broken module
is skipped with a warning instead of taking the session down.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from zenagent.agent.loop import AgentTool

__all__ = [
    "ExtensionAPI",
    "ExtensionCommand",
    "LoadedExtensions",
    "load_extensions",
]


@dataclass
class ExtensionCommand:
    """A slash command contributed by an extension."""

    name: str
    description: str
    handler: Callable


class ExtensionAPI:
    """The surface handed to an extension's ``register(api)`` function.

    Deliberately narrow: extensions cannot reach into the agent loop, they can
    only contribute tools, commands, prompt text and event listeners.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self.tools: list[AgentTool] = []
        self.commands: dict[str, ExtensionCommand] = {}
        self.prompt_fragments: list[str] = []
        self.hooks: dict[str, list[Callable]] = {}

    def register_tool(self, tool: AgentTool) -> None:
        self.tools.append(tool)

    def register_command(self, name: str, description: str, handler: Callable) -> None:
        self.commands[name] = ExtensionCommand(name=name, description=description, handler=handler)

    def add_system_prompt(self, text: str) -> None:
        if text.strip():
            self.prompt_fragments.append(text.strip())

    def on(self, event_type: str, callback: Callable[[Any], None]) -> None:
        self.hooks.setdefault(event_type, []).append(callback)


@dataclass
class LoadedExtensions:
    """Everything the enabled extensions contributed."""

    tools: list[AgentTool] = field(default_factory=list)
    commands: dict[str, ExtensionCommand] = field(default_factory=dict)
    prompt_fragments: list[str] = field(default_factory=list)
    hooks: dict[str, list[Callable]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def prompt_text(self) -> str:
        if not self.prompt_fragments:
            return ""
        return "\n\n".join(self.prompt_fragments)

    def __bool__(self) -> bool:
        return bool(self.tools or self.commands or self.prompt_fragments or self.hooks)


def load_extensions(paths: list[str | Path]) -> LoadedExtensions:
    """Import each extension module and merge what it registered."""
    loaded = LoadedExtensions()
    for position, raw_path in enumerate(paths):
        path = Path(raw_path).expanduser().resolve()
        if not path.is_file():
            loaded.warnings.append(f"{path}: not found")
            continue
        module_name = f"zenagent_extension_{path.stem}_{position}"
        api = ExtensionAPI(path.stem)
        try:
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                raise ImportError("cannot create a module spec")
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            register = getattr(module, "register", None)
            if not callable(register):
                raise AttributeError(  # noqa: TRY004 - a missing attribute is not a type error
                    "extension has no register(api) function"
                )
            register(api)
        except Exception as exc:  # noqa: BLE001 - one bad extension must not break the session
            sys.modules.pop(module_name, None)
            loaded.warnings.append(f"{path}: {type(exc).__name__}: {exc}")
            continue

        loaded.tools.extend(api.tools)
        for name, command in api.commands.items():
            if name in loaded.commands:
                loaded.warnings.append(
                    f"{path}: command {name!r} already registered (keeping the first)"
                )
                continue
            loaded.commands[name] = command
        loaded.prompt_fragments.extend(api.prompt_fragments)
        for event_type, callbacks in api.hooks.items():
            loaded.hooks.setdefault(event_type, []).extend(callbacks)
    return loaded
