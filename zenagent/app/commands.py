"""Slash-command registry shared by REPL, TUI and future frontends."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from zenagent.agent.session import session_title
from zenagent.app.appearance import THEME_CHOICES
from zenagent.app.chat import ChatController
from zenagent.app.models import ModelManager
from zenagent.app.modes import describe_modes
from zenagent.app.runtime import Runtime
from zenagent.app.sessions import SessionManager
from zenagent.i18n import LANGUAGE_CHOICES, t

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
    #: When set, the frontend switches to this appearance (auto|dark|light).
    appearance: str | None = None
    #: When set, the frontend switches to this interface language.
    language: str | None = None


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
            return CommandResult(
                message=t("cmdmsg.unknown_command", name=name)
            )
        return await command.handler(self._services, argument)


def _command(name: str):
    def decorate(handler):
        def factory(services: AppServices) -> Command:
            return Command(
                name=name,
                description=t(f"cmd.{name}.description"),
                handler=handler,
            )

        factory.name = name  # type: ignore[attr-defined]
        return factory

    return decorate


@_command("help")
async def _help(services: AppServices, argument: str) -> CommandResult:
    return CommandResult(show_help=True)


@_command("model")
async def _model(services: AppServices, argument: str) -> CommandResult:
    return CommandResult(screen="model-picker")


@_command("mode")
async def _mode(services: AppServices, argument: str) -> CommandResult:
    requested = argument.strip()
    if not requested:
        # TUI opens a selector; REPL prints the current mode plus the vocabulary.
        return CommandResult(screen="mode-picker")
    try:
        mode = services.runtime.set_mode(requested)
    except ValueError as exc:
        return CommandResult(message=str(exc))
    return CommandResult(
        message=t("cmdmsg.mode_set", mode=mode, modes=describe_modes())
    )


@_command("lang")
async def _lang(services: AppServices, argument: str) -> CommandResult:
    requested = argument.strip().lower()
    if not requested:
        return CommandResult(screen="language-picker")
    if requested not in LANGUAGE_CHOICES:
        return CommandResult(
            message=t(
                "cmdmsg.lang_unknown",
                requested=requested,
                choices="|".join(LANGUAGE_CHOICES),
            )
        )
    return CommandResult(language=requested)


@_command("theme")
async def _theme(services: AppServices, argument: str) -> CommandResult:
    requested = argument.strip().lower()
    if not requested:
        # TUI opens a selector; REPL prints the current theme.
        return CommandResult(screen="theme-picker")
    if requested not in THEME_CHOICES:
        return CommandResult(
            message=t(
                "cmdmsg.theme_unknown",
                requested=requested,
                choices="|".join(THEME_CHOICES),
            )
        )
    return CommandResult(appearance=requested)


@_command("sessions")
async def _sessions(services: AppServices, argument: str) -> CommandResult:
    if argument.strip().lower() == "all":
        return CommandResult(screen="session-picker-all")
    return CommandResult(screen="session-picker")


@_command("rename")
async def _rename(services: AppServices, argument: str) -> CommandResult:
    current = services.sessions.current()
    requested = argument.strip()
    if not requested:
        return CommandResult(
            message=t("cmdmsg.session_name", name=session_title(current))
        )
    try:
        stored = services.sessions.rename(current, requested)
    except ValueError as exc:
        return CommandResult(message=str(exc))
    return CommandResult(message=t("cmdmsg.renamed", name=stored))


@_command("pin")
async def _pin(services: AppServices, argument: str) -> CommandResult:
    from zenagent.agent.session import session_title

    requested = argument.strip().lower()
    current = services.sessions.current()
    try:
        if requested in ("on", "off"):
            state = services.sessions.set_pin(current, requested == "on")
        elif requested:
            return CommandResult(
                message=t("cmdmsg.pin_unknown", requested=requested)
            )
        else:
            state = services.sessions.toggle_pin(current)
    except ValueError as exc:
        return CommandResult(message=str(exc))
    return CommandResult(
        message=t(
            "cmdmsg.pinned" if state else "cmdmsg.unpinned",
            name=session_title(current),
        )
    )


@_command("clear")
async def _clear(services: AppServices, argument: str) -> CommandResult:
    services.chat.clear()
    return CommandResult(message=t("cmdmsg.cleared"))


@_command("compact")
async def _compact(services: AppServices, argument: str) -> CommandResult:
    try:
        message = await services.chat.compact_now()
    except (ValueError, RuntimeError) as exc:
        return CommandResult(message=t("cmdmsg.compaction_failed", error=exc))
    return CommandResult(
        message=message or t("cmdmsg.nothing_to_compact"), reset_transcript=True
    )


@_command("image")
async def _image(services: AppServices, argument: str) -> CommandResult:
    from pathlib import Path

    from zenagent.attachments import read_image_argument

    path = argument.strip()
    if not path:
        return CommandResult(message=t("cmdmsg.image_usage"))
    model = services.runtime.agent.model
    if not model.supports_vision:
        return CommandResult(
            message=t("cmdmsg.image_not_supported", model=model.id)
        )
    try:
        block = read_image_argument(Path(path).expanduser())
    except ValueError as exc:
        return CommandResult(message=str(exc))
    services.chat.attach_image(block)
    return CommandResult(
        message=t(
            "cmdmsg.image_attached",
            mime=block.mime_type,
            count=len(services.chat.attachments),
        )
    )


@_command("images")
async def _images(services: AppServices, argument: str) -> CommandResult:
    if argument.strip() == "clear":
        services.chat.clear_attachments()
        return CommandResult(message=t("cmdmsg.images_cleared"))
    blocks = services.chat.attachments
    if not blocks:
        return CommandResult(message=t("cmdmsg.no_images"))
    rows = [
        t(
            "cmdmsg.image_row",
            position=position,
            mime=block.mime_type,
            chars=len(block.data),
        )
        for position, block in enumerate(blocks, start=1)
    ]
    return CommandResult(message="\n".join(rows))


@_command("mcp")
async def _mcp(services: AppServices, argument: str) -> CommandResult:
    manager = services.runtime.mcp
    if manager is None:
        return CommandResult(message=t("cmdmsg.no_mcp"))
    streaming = services.runtime.agent.is_streaming
    if not streaming:
        try:
            await services.runtime.refresh_mcp()
        except RuntimeError as exc:  # a run started while we waited
            return CommandResult(message=str(exc))
    connected = ", ".join(manager.connected_names) or "(none)"
    suffix = " · tools not refreshed while the agent is working" if streaming else ""
    return CommandResult(
        message=t("cmdmsg.mcp_connected", connected=connected, suffix=suffix)
    )


@_command("skills")
async def _skills(services: AppServices, argument: str) -> CommandResult:
    """Rescan before listing, so a skill added on disk shows up immediately.

    The rescan is a plain directory + frontmatter read (no extension code runs),
    and it also swaps ``read_skill``'s roster while the agent is idle -- a list
    that disagreed with what the model can actually load would be a lie.
    """
    if not services.runtime.skills_enabled:
        return CommandResult(message=t("cmdmsg.skills_disabled"))
    notes = services.runtime.refresh_skills()
    skills = services.runtime.skills
    if not skills:
        # Keep the notes: "skills 1 -> 0 (-gone)" is the whole point of the line.
        return CommandResult(
            message="\n".join(
                [
                    *notes,
                    t("cmdmsg.no_skills"),
                ]
            )
        )
    lines = [
        t("cmdmsg.skill_row", name=skill.name, description=skill.description)
        for skill in skills
    ]
    if notes:
        lines.insert(0, "; ".join(notes))
    return CommandResult(message="\n".join(lines))


@_command("reload")
async def _reload(services: AppServices, argument: str) -> CommandResult:
    if services.runtime.agent.is_streaming:
        return CommandResult(message=t("cmdmsg.cannot_reload"))
    # MCP retries can take seconds; a run may start while we wait, and swapping
    # tools under it must not happen (and must not escape as an exception the
    # TUI would treat as fatal).
    try:
        await services.runtime.refresh_mcp()
        notes = services.runtime.rediscover()
    except RuntimeError as exc:
        return CommandResult(message=str(exc))
    return CommandResult(message=t("cmdmsg.reloaded", notes=" · ".join(notes)))


@_command("export")
async def _export(services: AppServices, argument: str) -> CommandResult:
    path = services.sessions.export(services.sessions.current(), argument or "md")
    return CommandResult(message=t("cmdmsg.exported", path=path))


@_command("doctor")
async def _doctor(services: AppServices, argument: str) -> CommandResult:
    return CommandResult(message=t("cmdmsg.doctor_hint"))


@_command("quit")
async def _quit(services: AppServices, argument: str) -> CommandResult:
    return CommandResult(quit=True)


@_command("exit")
async def _exit(services: AppServices, argument: str) -> CommandResult:
    return CommandResult(quit=True)


THINKING_LEVELS = ("off", "low", "medium", "high", "max")

@_command("thinking")
async def _thinking(services: AppServices, argument: str) -> CommandResult:
    agent = services.runtime.agent
    level = argument.strip().lower()
    if not level:
        # TUI opens a selector; the levels are the shared command vocabulary.
        return CommandResult(screen="thinking-picker")
    if level not in THINKING_LEVELS:
        return CommandResult(
            message=t(
                "cmdmsg.thinking_unknown",
                level=level,
                choices="|".join(THINKING_LEVELS),
            )
        )
    agent.thinking = None if level == "off" else level
    return CommandResult(message=t("cmdmsg.thinking_set", level=level))


@_command("tools")
async def _tools(services: AppServices, argument: str) -> CommandResult:
    from zenagent.app.support import describe_tools

    return CommandResult(message=describe_tools(services.runtime.agent.tools))


@_command("session")
async def _session(services: AppServices, argument: str) -> CommandResult:
    status = services.chat.status
    window = status.context_window or 1
    return CommandResult(
        message=t(
            "cmdmsg.session_info",
            name=status.session_name,
            model=status.model_id,
            thinking=status.thinking or "off",
            used=status.context_used,
            window=status.context_window,
            pct=status.context_used * 100 / window,
            cost=f"{status.cost:.4f}",
            input=status.input_tokens,
            output=status.output_tokens,
        )
    )


@_command("usage")
async def _usage(services: AppServices, argument: str) -> CommandResult:
    status = services.chat.status
    return CommandResult(
        message=(
            t(
                "cmdmsg.usage_info",
                input=status.input_tokens,
                output=status.output_tokens,
                cache=status.cache_read,
                cost=f"{status.cost:.4f}",
            )
        )
    )


@_command("context")
async def _context(services: AppServices, argument: str) -> CommandResult:
    status = services.chat.status
    window = status.context_window or 1
    percent = status.context_used * 100 / window
    remaining = max(0, status.context_window - status.context_used)
    return CommandResult(
        message=t(
            "cmdmsg.context_info",
            used=status.context_used,
            window=status.context_window,
            pct=percent,
            left=remaining,
        )
    )


@_command("search")
async def _search(services: AppServices, argument: str) -> CommandResult:
    from zenagent.tools.web import format_results, run_search
    from zenagent.tools.web.registry import default_provider

    query = argument.strip()
    if not query or query in ("settings", "provider", "status"):
        chosen = default_provider() or "auto"
        return CommandResult(
            message=t("cmdmsg.search_provider_info", provider=chosen),
            screen="search-settings",
        )
    result = await run_search(query, limit=5)
    return CommandResult(message=format_results(result))


@_command("hotkeys")
async def _hotkeys(services: AppServices, argument: str) -> CommandResult:
    return CommandResult(message=t("cmdmsg.hotkeys_text"))


@_command("undo")
async def _undo(services: AppServices, argument: str) -> CommandResult:
    if services.chat.status.running:
        return CommandResult(message=t("cmdmsg.cannot_undo"))
    result = services.sessions.undo()
    if result is None:
        return CommandResult(message=t("cmdmsg.nothing_to_undo"))
    dropped, path = result
    services.chat.reload_transcript()
    return CommandResult(
        message=t("cmdmsg.undone", count=dropped, path=path.name),
        reset_transcript=True,
    )


@_command("retry")
async def _retry(services: AppServices, argument: str) -> CommandResult:
    if services.chat.status.running:
        return CommandResult(message=t("cmdmsg.already_working"))
    text = services.chat.retry()
    if text is None:
        return CommandResult(message=t("cmdmsg.nothing_to_retry"))
    preview = text if len(text) <= 60 else text[:59] + "…"
    return CommandResult(
        message=t("cmdmsg.retrying", preview=preview), reset_transcript=True
    )


@_command("new")
async def _new(services: AppServices, argument: str) -> CommandResult:
    path = await services.sessions.new()
    services.chat.reload_transcript()
    return CommandResult(
        message=t("cmdmsg.new_session", path=path.name), reset_transcript=True
    )


@_command("fork")
async def _fork(services: AppServices, argument: str) -> CommandResult:
    path = await services.sessions.fork()
    services.chat.reload_transcript()
    return CommandResult(
        message=t("cmdmsg.forked", path=path.name), reset_transcript=True
    )


@_command("settings")
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
        _pin,
        _clear,
        _compact,
        _image,
        _images,
        _mcp,
        _skills,
        _reload,
        _export,
        _doctor,
        _quit,
        _exit,
        _thinking,
        _tools,
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
        _theme,
        _lang,
    ):
        registry.register(factory(services))
    return registry
