"""Slash-command registry shared by REPL, TUI and future frontends."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from zenagent.agent.session import Session, session_title
from zenagent.app.appearance import THEME_CHOICES
from zenagent.app.chat import ChatController
from zenagent.app.links import extract_links, parse_index
from zenagent.app.models import ModelManager
from zenagent.app.modes import describe_modes
from zenagent.app.restore import run_restore_command
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
    #: When set, the frontend switches models (the picker and the argument path
    #: share one implementation, so both heal the same state).
    model: str | None = None


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
    requested = argument.strip()
    if not requested:
        return CommandResult(screen="model-picker")
    # The value path mirrors `/mode <value>`: the argument is applied directly
    # instead of opening the picker again (the frontend owns the switch so the
    # hint path and the picker cannot drift).
    return CommandResult(model=requested)


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
    if argument.strip().lower() in ("add", "wizard"):
        return CommandResult(
            message=t("cmdmsg.mcp_add_hint"), screen="mcp-add"
        )
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
        notes = await services.runtime.reconcile_async()
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
        ),
        screen="usage-dashboard",
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
    return CommandResult(message=t("cmdmsg.hotkeys_text"), screen="hotkeys")


@_command("undo")
async def _undo(services: AppServices, argument: str) -> CommandResult:
    if services.chat.status.running:
        return CommandResult(message=t("cmdmsg.cannot_undo"))
    result = services.sessions.undo()
    if result is None:
        return CommandResult(message=t("cmdmsg.nothing_to_undo"))
    dropped, path = result
    services.chat.reload_transcript(drop_pending=True)
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
    services.chat.reload_transcript(drop_pending=True)
    return CommandResult(
        message=t("cmdmsg.new_session", path=path.name), reset_transcript=True
    )


@_command("fork")
async def _fork(services: AppServices, argument: str) -> CommandResult:
    path = await services.sessions.fork()
    services.chat.reload_transcript(drop_pending=True)
    return CommandResult(
        message=t("cmdmsg.forked", path=path.name), reset_transcript=True
    )


@_command("restore")
async def _restore(services: AppServices, argument: str) -> CommandResult:
    message = run_restore_command(
        services.runtime.journal,
        argument,
        running=services.chat.status.running,
    )
    return CommandResult(message=message)


@_command("todo")
async def _todo(services: AppServices, argument: str) -> CommandResult:
    """Open the editable todo list (the model shares the same sidecar)."""
    return CommandResult(screen="todo-edit")


@_command("settings")
async def _settings(services: AppServices, argument: str) -> CommandResult:
    return CommandResult(screen="settings")


@_command("plan-review")
async def _plan_review(services: AppServices, argument: str) -> CommandResult:
    """Review the plan the model produced in plan mode (approve → full)."""
    plan = services.chat.last_assistant_text()
    if not plan:
        return CommandResult(message=t("cmdmsg.no_plan"))
    return CommandResult(screen="plan-review")


@_command("import")
async def _import(services: AppServices, argument: str) -> CommandResult:
    """Load a session file and continue it (the REPL's /import, for the TUI)."""
    raw = argument.strip()
    if not raw:
        return CommandResult(message=t("cmdmsg.import_usage"))
    target = Path(raw).expanduser()
    if not target.is_file():
        return CommandResult(message=t("cmdmsg.import_missing", path=target))
    try:
        await services.runtime.switch_session(target)
    except Exception as exc:  # noqa: BLE001 - a bad file must not exit the app
        return CommandResult(message=t("cmdmsg.import_failed", error=exc))
    services.chat.reload_transcript(drop_pending=True)
    return CommandResult(
        message=t("cmdmsg.imported", path=target.name), reset_transcript=True
    )



@_command("jobs")
async def _jobs(services: AppServices, argument: str) -> CommandResult:
    """List background jobs, or kill one with ``/jobs kill <id>``."""
    from zenagent.tools.jobs import UnknownJobError, get_job_manager

    raw = argument.strip()
    manager = get_job_manager()
    if raw.startswith("kill"):
        job_id = raw[len("kill") :].strip()
        if not job_id:
            return CommandResult(message=t("cmdmsg.jobs_usage"))
        try:
            job = await manager.kill(job_id)
        except UnknownJobError as exc:
            return CommandResult(message=str(exc))
        return CommandResult(message=t("cmdmsg.job_killed", id=job.id))
    jobs = manager.list()
    if not jobs:
        return CommandResult(message=t("cmdmsg.no_jobs"))
    rows = [
        t("cmdmsg.jobs_row", id=job.id, status=job.status, command=job.command[:70])
        for job in jobs
    ]
    return CommandResult(message="\n".join(rows))


@_command("copy")
async def _copy(services: AppServices, argument: str) -> CommandResult:
    """Copy the argument (or the last answer, or link <n> of it) to the clipboard."""
    from zenagent.cli.tui.clipboard import write_clipboard_text

    # A bare number is a link index (`/urls` numbers them); anything else keeps
    # the old "copy this text" meaning, and no argument means the last answer.
    index = parse_index(argument)
    if index is not None:
        links = extract_links(services.chat.last_assistant_text())
        if not links:
            return CommandResult(message=t("cmdmsg.urls_empty"))
        if not 1 <= index <= len(links):
            return CommandResult(message=t("cmdmsg.url_index_invalid", count=len(links)))
        link = links[index - 1]
        if write_clipboard_text(link):
            return CommandResult(message=t("cmdmsg.url_copied", url=link))
        return CommandResult(message=t("cmdmsg.copy_failed"))

    text = argument.strip() or services.chat.last_assistant_text()
    if not text:
        return CommandResult(message=t("cmdmsg.copy_empty"))
    if write_clipboard_text(text):
        return CommandResult(message=t("cmdmsg.copied"))
    return CommandResult(message=t("cmdmsg.copy_failed"))


#: `/urls` preview width; copying goes through `/copy <n>` and is never clipped.
URL_PREVIEW_CHARS = 80


@_command("urls")
async def _urls(services: AppServices, argument: str) -> CommandResult:
    """List the links of the last answer, numbered for `/copy` and `/open`."""
    links = extract_links(services.chat.last_assistant_text())
    if not links:
        return CommandResult(message=t("cmdmsg.urls_empty"))
    rows = []
    for index, url in enumerate(links, start=1):
        if len(url) <= URL_PREVIEW_CHARS:
            preview = url
        else:
            preview = url[: URL_PREVIEW_CHARS - 1] + "…"
        rows.append(t("cmdmsg.urls_row", index=index, preview=preview, length=len(url)))
    return CommandResult(message="\n".join(rows))


@_command("open")
async def _open(services: AppServices, argument: str) -> CommandResult:
    """Open link <n> of the last answer in the default browser."""
    import webbrowser

    index = parse_index(argument)
    if index is None or index < 1:
        return CommandResult(message=t("cmdmsg.url_usage"))
    links = extract_links(services.chat.last_assistant_text())
    if not links:
        return CommandResult(message=t("cmdmsg.urls_empty"))
    if index > len(links):
        return CommandResult(message=t("cmdmsg.url_index_invalid", count=len(links)))
    url = links[index - 1]
    try:
        opened = webbrowser.open(url)
    except Exception:  # noqa: BLE001 - a missing browser must not kill the TUI
        opened = False
    if not opened:
        return CommandResult(message=t("cmdmsg.url_open_failed", url=url))
    return CommandResult(message=t("cmdmsg.url_opened", url=url))


@_command("shake")
async def _shake(services: AppServices, argument: str) -> CommandResult:
    """Drop one class of heavy content without summarizing (tools/images/thinking)."""
    from zenagent.app.shake import KINDS, shake

    raw = argument.strip().lower()
    if raw in ("", "all"):
        kinds = set(KINDS)
    elif raw in KINDS:
        kinds = {raw}
    else:
        return CommandResult(message=t("cmdmsg.shake_usage"))
    try:
        changed, session = shake(services.runtime.agent, kinds)
    except ValueError as exc:
        return CommandResult(message=str(exc))
    services.runtime.session = session
    services.chat.reload_transcript(drop_pending=True)
    return CommandResult(
        message=t("cmdmsg.shaken", count=changed, path=session.path.name),
        reset_transcript=True,
    )


@_command("btw")
async def _btw(services: AppServices, argument: str) -> CommandResult:
    """Ask a side question without touching the main transcript."""
    from zenagent.app.btw import ask_side_question

    if not argument.strip():
        return CommandResult(message=t("cmdmsg.btw_usage"))
    try:
        answer = await ask_side_question(services.runtime.agent, argument)
    except (ValueError, RuntimeError) as exc:
        return CommandResult(message=str(exc))
    return CommandResult(message=answer)


@_command("add-dir")
async def _add_dir(services: AppServices, argument: str) -> CommandResult:
    raw = argument.strip()
    if not raw:
        return CommandResult(message=t("cmdmsg.dirs_usage"))
    path = Path(raw).expanduser().resolve()
    if not path.is_dir():
        return CommandResult(message=t("cmdmsg.dir_missing", path=path))
    if path not in services.runtime.extra_dirs:
        services.runtime.extra_dirs.append(path)
    return CommandResult(message=t("cmdmsg.dir_added", path=path))


@_command("remove-dir")
async def _remove_dir(services: AppServices, argument: str) -> CommandResult:
    raw = argument.strip()
    if not raw:
        return CommandResult(message=t("cmdmsg.dirs_usage"))
    path = Path(raw).expanduser().resolve()
    if path in services.runtime.extra_dirs:
        services.runtime.extra_dirs.remove(path)
        return CommandResult(message=t("cmdmsg.dir_removed", path=path))
    return CommandResult(message=t("cmdmsg.dir_missing", path=path))


@_command("dirs")
async def _dirs(services: AppServices, argument: str) -> CommandResult:
    dirs = [str(Path.cwd()), *(str(path) for path in services.runtime.extra_dirs)]
    return CommandResult(message=t("cmdmsg.dirs", dirs="\n".join(dirs)))


@_command("fresh")
async def _fresh(services: AppServices, argument: str) -> CommandResult:
    """Reset transient display state (hidden cards, collapsed thinking, pending)."""
    services.chat.drop_pending()
    return CommandResult(message=t("cmdmsg.fresh"), screen="display-reset")


@_command("handoff")
async def _handoff(services: AppServices, argument: str) -> CommandResult:
    """Summarize the session into a handoff document, then compact in place."""
    from zenagent.app.compact import compact
    from zenagent.types import TextContent

    agent = services.runtime.agent
    try:
        path = await compact(agent)
    except (ValueError, RuntimeError) as exc:
        return CommandResult(message=str(exc))
    summary = "\n".join(
        block.text
        for message in agent.messages
        for block in getattr(message, "content", [])
        if isinstance(block, TextContent)
    ).strip()
    document = Path.cwd() / f"handoff-{time.strftime('%Y%m%d-%H%M%S')}.md"
    try:
        document.write_text(f"# Handoff\n\n{summary}\n", encoding="utf-8")
    except OSError as exc:
        return CommandResult(message=str(exc))
    services.runtime.session = Session(path)
    services.chat.reload_transcript(drop_pending=True)
    return CommandResult(
        message=t("cmdmsg.handoff", path=document.name), reset_transcript=True
    )


@_command("trace")
async def _trace(services: AppServices, argument: str) -> CommandResult:
    path = getattr(services.runtime, "trace_path", None)
    if not path:
        return CommandResult(message=t("cmdmsg.trace_off"))
    return CommandResult(message=t("cmdmsg.trace_path", path=path))


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
        _import,
        _settings,
        _plan_review,
        _todo,
        _undo,
        _restore,
        _retry,
        _search,
        _theme,
        _lang,
        _jobs,
        _copy,
        _urls,
        _open,
        _shake,
        _btw,
        _add_dir,
        _remove_dir,
        _dirs,
        _fresh,
        _handoff,
        _trace,
    ):
        registry.register(factory(services))
    return registry
