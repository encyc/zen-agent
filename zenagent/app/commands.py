"""Slash-command registry shared by REPL, TUI and future frontends."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from zenagent.app.chat import ChatController
from zenagent.app.models import ModelManager
from zenagent.app.runtime import Runtime
from zenagent.app.sessions import SessionManager

__all__ = [
    "AppServices",
    "Command",
    "CommandRegistry",
    "CommandResult",
    "build_default_commands",
]


@dataclass
class AppServices:
    """The app-layer objects a command may touch."""

    runtime: Runtime
    chat: ChatController
    models: ModelManager
    sessions: SessionManager


@dataclass
class CommandResult:
    """What the UI should do after a command runs."""

    message: str | None = None
    screen: str | None = None
    quit: bool = False


@dataclass
class Command:
    name: str
    description: str
    handler: Callable[[AppServices, str], Awaitable[CommandResult]]


class CommandRegistry:
    """Name → handler, with a single dispatch point for every frontend."""

    def __init__(self, services: AppServices) -> None:
        self._services = services
        self._commands: dict[str, Command] = {}

    def register(self, command: Command) -> None:
        self._commands[command.name] = command

    def names(self) -> list[Command]:
        return sorted(self._commands.values(), key=lambda command: command.name)

    def get(self, name: str) -> Command | None:
        return self._commands.get(name)

    async def dispatch(self, name: str, argument: str = "") -> CommandResult:
        command = self._commands.get(name)
        if command is None:
            return CommandResult(message=f"unknown command: {name} (try help)")
        return await command.handler(self._services, argument)


def _command(name: str, description: str):
    def decorate(handler):
        def factory(services: AppServices) -> Command:
            return Command(name=name, description=description, handler=handler)

        factory.name = name  # type: ignore[attr-defined]
        return factory

    return decorate


@_command("help", "show commands and keybindings")
async def _help(services: AppServices, argument: str) -> CommandResult:
    return CommandResult(message="help")


@_command("model", "browse and switch models")
async def _model(services: AppServices, argument: str) -> CommandResult:
    return CommandResult(screen="model-picker")


@_command("sessions", "browse, resume or fork sessions")
async def _sessions(services: AppServices, argument: str) -> CommandResult:
    return CommandResult(screen="session-picker")


@_command("clear", "clear the visible conversation")
async def _clear(services: AppServices, argument: str) -> CommandResult:
    services.chat.clear()
    return CommandResult(message="cleared the conversation")


@_command("compact", "summarize the conversation now")
async def _compact(services: AppServices, argument: str) -> CommandResult:
    return CommandResult(message="compaction runs after the current turn")


@_command("mcp", "reconnect MCP servers and refresh tools")
async def _mcp(services: AppServices, argument: str) -> CommandResult:
    await services.runtime.refresh_mcp()
    manager = services.runtime.mcp
    if manager is None:
        return CommandResult(message="no MCP servers configured")
    connected = ", ".join(manager.connected_names) or "(none)"
    return CommandResult(message=f"mcp connected: {connected}")


@_command("skills", "list discovered skills")
async def _skills(services: AppServices, argument: str) -> CommandResult:
    skills = services.runtime.skills
    if not skills:
        return CommandResult(message="no skills found")
    return CommandResult(message="\n".join(f"- {skill.name}: {skill.description}" for skill in skills))


@_command("reload", "re-discover skills, templates and extensions")
async def _reload(services: AppServices, argument: str) -> CommandResult:
    await services.runtime.refresh_mcp()
    return CommandResult(message="reloaded")


@_command("export", "export the current session as markdown")
async def _export(services: AppServices, argument: str) -> CommandResult:
    path = services.sessions.export(services.sessions.current(), argument or "md")
    return CommandResult(message=f"exported: {path}")


@_command("doctor", "run environment checks")
async def _doctor(services: AppServices, argument: str) -> CommandResult:
    return CommandResult(message="run `zen doctor` for the full report")


@_command("quit", "leave the app")
async def _quit(services: AppServices, argument: str) -> CommandResult:
    return CommandResult(quit=True)


def build_default_commands(services: AppServices) -> CommandRegistry:
    """Build the built-in registry for one app instance."""
    registry = CommandRegistry(services)
    for factory in (
        _help,
        _model,
        _sessions,
        _clear,
        _compact,
        _mcp,
        _skills,
        _reload,
        _export,
        _doctor,
        _quit,
    ):
        registry.register(factory(services))
    return registry
