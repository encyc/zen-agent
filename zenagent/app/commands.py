"""Slash-command registry shared by REPL, TUI and future frontends."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from zenagent.agent.session import session_title
from zenagent.app.chat import ChatController
from zenagent.app.models import ModelManager
from zenagent.app.modes import describe_modes
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
    show_help: bool = False
    reset_transcript: bool = False


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


@_command("help", "list every command")
async def _help(services: AppServices, argument: str) -> CommandResult:
    return CommandResult(show_help=True)


@_command("model", "browse and switch models")
async def _model(services: AppServices, argument: str) -> CommandResult:
    return CommandResult(screen="model-picker")


@_command("mode", "set runtime mode: plan|ask|full")
async def _mode(services: AppServices, argument: str) -> CommandResult:
    requested = argument.strip()
    if not requested:
        return CommandResult(
            message=f"mode: {services.runtime.mode} · {describe_modes()}"
        )
    try:
        mode = services.runtime.set_mode(requested)
    except ValueError as exc:
        return CommandResult(message=str(exc))
    return CommandResult(message=f"mode: {mode} · {describe_modes()}")


@_command("sessions", "browse, resume or fork sessions")
async def _sessions(services: AppServices, argument: str) -> CommandResult:
    return CommandResult(screen="session-picker")


@_command("rename", "rename the current session")
async def _rename(services: AppServices, argument: str) -> CommandResult:
    current = services.sessions.current()
    requested = argument.strip()
    if not requested:
        return CommandResult(message=f"session name: {session_title(current)}")
    try:
        stored = services.sessions.rename(current, requested)
    except ValueError as exc:
        return CommandResult(message=str(exc))
    return CommandResult(message=f"renamed: {stored}")


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


@_command("exit", "leave the app (alias of quit)")
async def _exit(services: AppServices, argument: str) -> CommandResult:
    return CommandResult(quit=True)


THINKING_LEVELS = ("off", "low", "medium", "high", "max")

HOTKEYS_TEXT = """\
enter send · shift+enter newline
esc interrupt · ctrl+c clear draft · ctrl+d quit
ctrl+q queue follow-up · ctrl+s steer · ctrl+x clear queue
ctrl+p next model · alt+p pick model · shift+tab thinking
ctrl+o expand tools · ctrl+t hide thinking
ctrl+k commands · ctrl+b info · pageup/pagedown scroll"""


@_command("thinking", "show or set reasoning: off|low|medium|high|max")
async def _thinking(services: AppServices, argument: str) -> CommandResult:
    agent = services.runtime.agent
    level = argument.strip().lower()
    if not level:
        return CommandResult(message=f"thinking: {agent.thinking or 'off'}")
    if level not in THINKING_LEVELS:
        return CommandResult(
            message=f"unknown thinking level: {level} (use {'|'.join(THINKING_LEVELS)})"
        )
    agent.thinking = None if level == "off" else level
    return CommandResult(message=f"thinking: {level}")


@_command("session", "show this session's model, context and spend")
async def _session(services: AppServices, argument: str) -> CommandResult:
    status = services.chat.status
    window = status.context_window or 1
    return CommandResult(
        message=(
            f"session: {status.session_name}\n"
            f"model: {status.model_id} · thinking {status.thinking or 'off'}\n"
            f"context: {status.context_used}/{status.context_window} "
            f"({status.context_used * 100 / window:.1f}%)\n"
            f"spend: ${status.cost:.4f} · {status.input_tokens} in / {status.output_tokens} out"
        )
    )


@_command("usage", "token and cost totals for this session")
async def _usage(services: AppServices, argument: str) -> CommandResult:
    status = services.chat.status
    return CommandResult(
        message=(
            f"in {status.input_tokens} · out {status.output_tokens} · "
            f"cache read {status.cache_read} · ${status.cost:.4f}"
        )
    )


@_command("context", "how full the context window is")
async def _context(services: AppServices, argument: str) -> CommandResult:
    status = services.chat.status
    window = status.context_window or 1
    percent = status.context_used * 100 / window
    remaining = max(0, status.context_window - status.context_used)
    return CommandResult(
        message=f"context: {status.context_used}/{status.context_window} "
        f"({percent:.1f}% used, {remaining} left)"
    )


@_command("search", "search the web, or open search settings")
async def _search(services: AppServices, argument: str) -> CommandResult:
    from zenagent.tools.web import format_results, run_search
    from zenagent.tools.web.registry import default_provider

    query = argument.strip()
    if not query or query in ("settings", "provider", "status"):
        chosen = default_provider() or "auto"
        return CommandResult(
            message=f"search provider: {chosen}",
            screen="search-settings",
        )
    result = await run_search(query, limit=5)
    return CommandResult(message=format_results(result))


@_command("hotkeys", "show the active keybindings")
async def _hotkeys(services: AppServices, argument: str) -> CommandResult:
    return CommandResult(message=HOTKEYS_TEXT)


@_command("undo", "drop the last turn (recoverable: new session file)")
async def _undo(services: AppServices, argument: str) -> CommandResult:
    if services.chat.status.running:
        return CommandResult(message="cannot undo while the agent is working")
    result = services.sessions.undo()
    if result is None:
        return CommandResult(message="nothing to undo")
    dropped, path = result
    services.chat.reload_transcript()
    return CommandResult(
        message=f"undid the last turn ({dropped} messages) → {path.name}",
        reset_transcript=True,
    )


@_command("retry", "re-run the last turn")
async def _retry(services: AppServices, argument: str) -> CommandResult:
    if services.chat.status.running:
        return CommandResult(message="already working")
    text = services.chat.retry()
    if text is None:
        return CommandResult(message="nothing to retry")
    preview = text if len(text) <= 60 else text[:59] + "…"
    return CommandResult(message=f"retrying: {preview}", reset_transcript=True)


@_command("new", "start a new session")
async def _new(services: AppServices, argument: str) -> CommandResult:
    path = await services.sessions.new()
    services.chat.reload_transcript()
    return CommandResult(message=f"new session: {path.name}", reset_transcript=True)


@_command("fork", "copy this session into a branch and switch to it")
async def _fork(services: AppServices, argument: str) -> CommandResult:
    path = await services.sessions.fork()
    services.chat.reload_transcript()
    return CommandResult(message=f"forked to {path.name}", reset_transcript=True)


@_command("settings", "open settings")
async def _settings(services: AppServices, argument: str) -> CommandResult:
    return CommandResult(screen="settings")


def build_default_commands(services: AppServices) -> CommandRegistry:
    """Build the built-in registry for one app instance."""
    registry = CommandRegistry(services)
    for factory in (
        _help,
        _model,
        _mode,
        _sessions,
        _rename,
        _clear,
        _compact,
        _mcp,
        _skills,
        _reload,
        _export,
        _doctor,
        _quit,
        _exit,
        _thinking,
        _session,
        _usage,
        _context,
        _hotkeys,
        _new,
        _fork,
        _settings,
        _undo,
        _retry,
        _search,
    ):
        registry.register(factory(services))
    return registry
