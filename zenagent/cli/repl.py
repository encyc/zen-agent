"""Interactive REPL: streamed answers, Ctrl+C, steering, slash commands.

Line-based (the full-screen TUI is explicitly out of scope): prompt_toolkit
reads input, rich formats tool feedback, and the Agent runs one turn at a time.
While a run is in flight, anything typed is queued as a *steering* message
instead of starting a second run.

Testability: ``input_fn`` and ``print_fn`` are injectable, so the logic can be
driven without a terminal.
"""

from __future__ import annotations

import asyncio
import inspect
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

from rich.console import Console

from zenagent.agent import Agent
from zenagent.agent.artifacts import build_artifact_sink
from zenagent.agent.session import (
    Session,
    latest_session,
    list_sessions,
    session_title,
    set_session_title,
    truncate_last_turn,
    unique_session_path,
)
from zenagent.app.compact import compact, maybe_auto_compact
from zenagent.app.modes import describe_modes, mode_prompt_note
from zenagent.app.retry import prepare_retry
from zenagent.app.skill_commands import build_skill_submission
from zenagent.app.support import describe_tools
from zenagent.attachments import read_image_argument
from zenagent.cli.completion import SLASH_COMMANDS, ReplCompleter, help_text
from zenagent.cli.keybindings import build_key_bindings, load_keybindings
from zenagent.cli.main import (
    _api_key_for,
    _system_prompt,
    resolve_model,
    session_id_for,
)
from zenagent.cli.permissions import PermissionGate
from zenagent.cli.render import (
    aggregate_usage,
    render_diff,
    status_line,
    tool_result_line,
)
from zenagent.cli.theme import DEFAULT_THEME, Theme, load_theme
from zenagent.config import ContextGuard, apply_user_config, load_user_config
from zenagent.context_files import (
    discover_context_files,
    load_context_files,
    system_prompt_with_context,
)
from zenagent.events import AgentEvent
from zenagent.extensions import load_extensions
from zenagent.mcp.config import load_mcp_config
from zenagent.mcp.manager import McpManager
from zenagent.providers.catalog import find_model, load_catalog
from zenagent.providers.registry import StreamFn
from zenagent.skills import (
    build_skill_tool,
    default_skill_roots,
    discover_skills,
    skills_prompt_index,
)
from zenagent.telemetry import MetricsRecorder, append_metrics
from zenagent.templates import default_template_roots, discover_templates, expand
from zenagent.tools import ALL_TOOLS
from zenagent.tools.ask import set_ask_handler
from zenagent.tools.task import build_task_tool
from zenagent.tools.todo import build_todo_tool
from zenagent.trace import TraceRecorder
from zenagent.types import ImageContent

__all__ = [
    "HELP_TEXT",
    "THINKING_LEVELS",
    "handle_slash",
    "repl",
    "split_input",
]

InputFn = Callable[[], Awaitable[str]]
PrintFn = Callable[..., None]

THINKING_LEVELS = ("off", "low", "medium", "high", "max")

HELP_TEXT = help_text()


def split_input(line: str) -> tuple[str, str]:
    """``("", text)`` for a prompt, or ``(command, argument)`` for ``/command``."""
    stripped = line.strip()
    if stripped.startswith("/"):
        command, _, argument = stripped[1:].partition(" ")
        return command.lower(), argument.strip()
    return "", stripped


async def handle_slash(
    agent: Agent,
    command: str,
    argument: str,
    *,
    print_fn: PrintFn = print,
    guard: ContextGuard | None = None,
    session_path: Path | None = None,
    session: Session | None = None,
    switch_session=None,
    images: list[ImageContent] | None = None,
    mcp_manager: McpManager | None = None,
    refresh_mcp=None,
    skill_list=None,
    skills_enabled: bool = True,
    template_list=None,
    extension_commands=None,
    reload_runtime=None,
    set_theme=None,
    keybindings=None,
    set_mode=None,
) -> str | None:
    """Run one slash command; returns ``"exit"`` when the REPL should stop."""
    if command in ("exit", "quit"):
        return "exit"
    if command in ("help", "?"):
        print_fn(HELP_TEXT)
        if template_list:
            print_fn("\nprompt templates:")
            for template in template_list:
                print_fn(f"  /{template.name}  {template.description}")
        return None
    if command == "mode":
        if set_mode is None:
            print_fn("mode switching is unavailable in this session")
            return None
        try:
            print_fn(set_mode(argument))
        except ValueError as exc:
            print_fn(f"mode: {exc}")
        return None
    if command == "rename":
        if session is None:
            print_fn("rename is unavailable in this session")
            return None
        requested = argument.strip()
        if not requested:
            print_fn(f"session name: {session_title(session.path)}")
            return None
        try:
            stored = set_session_title(session.path, requested)
        except ValueError as exc:
            print_fn(f"rename: {exc}")
        else:
            print_fn(f"renamed: {stored}")
        return None
    if command == "clear":
        agent.reset()
        print_fn("cleared the conversation")
        return None
    if command == "model":
        if not argument:
            print_fn(f"current model: {agent.model.id}")
            return None
        try:
            agent.model = find_model(load_catalog(), argument)
        except KeyError:
            print_fn(f"unknown model: {argument}")
        else:
            if guard is not None:
                guard.context_window = agent.model.context_window
            print_fn(f"model -> {agent.model.id} (next turn)")
        return None
    if command == "thinking":
        if not argument:
            print_fn(
                f"thinking: {agent.thinking or 'off'} · off|low|medium|high|max"
            )
            return None
        level = argument
        if level not in THINKING_LEVELS:
            print_fn("usage: /thinking off|low|medium|high|max")
            return None
        agent.thinking = level
        print_fn(f"thinking -> {level}")
        return None
    if command == "sessions":
        scope = "all" if argument.strip().lower() == "all" else "project"
        infos = list_sessions(scope=scope)
        if not infos:
            print_fn("no saved sessions")
            if scope == "project":
                print_fn("hint: /sessions all lists every project")
            return None
        current = session.path if session is not None else None
        for position, info in enumerate(infos, start=1):
            mark = "*" if info.path == current else " "
            label = f"[{info.project or 'legacy'}] " if scope == "all" else ""
            title = info.title or "(empty)"
            print_fn(
                f"{mark} {position:2d}. {label}{title} · "
                f"{info.messages} msgs · {info.path.name}"
            )
        return None
    if command == "fork":
        if session is None:
            print_fn("fork is unavailable in this session")
            return None
        try:
            index = int(argument)
        except ValueError:
            print_fn("usage: /fork <n>   (n = 1-based user message to branch after)")
            return None
        try:
            forked_path = session.fork_at(index)
        except ValueError as exc:
            print_fn(str(exc))
            return None
        forked = Session(forked_path)
        agent.replace_messages(forked.load())
        if switch_session is not None:
            switch_session(forked)
        print_fn(f"forked at message {index} -> {forked_path.name}")
        return None
    if command == "import":
        if not argument:
            print_fn("usage: /import <path>")
            return None
        imported_path = Path(argument).expanduser()
        if not imported_path.is_file():
            print_fn(f"no such session: {imported_path}")
            return None
        imported = Session(imported_path)
        messages = imported.load()
        for warning in imported.warnings:
            print_fn(warning)
        agent.replace_messages(messages)
        if switch_session is not None:
            switch_session(imported)
        print_fn(f"imported {len(messages)} messages from {imported_path.name}")
        return None
    if command == "undo":
        if agent.is_streaming:
            print_fn("cannot undo while the agent is working")
            return None
        truncated = truncate_last_turn(agent.messages)
        dropped = len(agent.messages) - len(truncated)
        if dropped == 0:
            print_fn("nothing to undo")
            return None
        agent.replace_messages(truncated)
        if session is not None:
            new_path = session.rewrite(truncated)
            if switch_session is not None:
                switch_session(Session(new_path))
            print_fn(f"undid the last turn ({dropped} messages) -> {new_path.name}")
        else:
            print_fn(f"undid the last turn ({dropped} messages)")
        return None
    if command == "session":
        print_fn(
            status_line(
                model_id=agent.model.id,
                thinking=agent.thinking,
                messages=len(agent.messages),
                usage=aggregate_usage(agent.messages),
                context_window=agent.model.context_window,
            )
        )
        if session_path is not None:
            print_fn(f"session: {session_path}")
        return None
    if command == "image":
        if images is None:
            print_fn("image attachments are unavailable here")
            return None
        if not agent.model.supports_vision:
            print_fn(f"{agent.model.id} does not accept images")
            return None
        if not argument:
            print_fn("usage: /image <path>")
            return None
        try:
            block = read_image_argument(Path(argument).expanduser())
        except ValueError as exc:
            print_fn(str(exc))
            return None
        images.append(block)
        print_fn(f"attached {argument} ({block.mime_type}); /images to review")
        return None
    if command == "images":
        if argument == "clear":
            if images is not None:
                images.clear()
            print_fn("cleared attached images")
            return None
        if not images:
            print_fn("no attached images")
            return None
        for position, block in enumerate(images, start=1):
            print_fn(f"{position}. {block.mime_type} ({len(block.data)} base64 chars)")
        return None
    if command == "theme":
        if set_theme is None:
            print_fn("themes are unavailable here")
            return None
        if not argument:
            print_fn("usage: /theme default|mono|solarized|<path.json>")
            return None
        set_theme(argument)
        return None
    if command == "hotkeys":
        if not keybindings:
            print_fn("no keybindings loaded")
            return None
        for action, key in sorted(keybindings.items()):
            print_fn(f"{action}: {key}")
        return None
    if command == "reload":
        if reload_runtime is None:
            print_fn("reload is unavailable here")
            return None
        await reload_runtime()
        return None
    if command == "skills":
        # Rescan for display only. The REPL's discovery is monolithic (skills,
        # templates and extensions in one pass), so adopting here would mean
        # re-executing extension files inside a display command; `/reload`
        # remains the operation that applies changes. Showing the disk truth and
        # naming what is pending is the honest middle: the list is never stale,
        # and the hint says how to make the model see it.
        if not skills_enabled:
            print_fn("skills are disabled (--no-skills)")
            return None
        fresh, _warnings = discover_skills(default_skill_roots(Path.cwd()))
        if not fresh:
            print_fn("no skills found (put SKILL.md files under .zenagent/skills)")
            return None
        for skill in fresh:
            print_fn(f"- {skill.name}: {skill.description}")

        # ``skill_list`` is None for callers that do not track what the agent
        # loaded; without it there is nothing to diff against, so no hint.
        if skill_list is not None:
            def signature(skills):
                return {
                    (skill.name, skill.description, skill.body) for skill in skills
                }

            pending = sorted(
                {item[0] for item in signature(fresh) ^ signature(skill_list)}
            )
            if pending:
                print_fn(
                    f"changed on disk ({', '.join(pending)}): /reload applies them"
                )
        return None
    if command == "mcp":
        if mcp_manager is None:
            print_fn("no MCP servers configured")
            return None
        if argument.startswith("retry") and refresh_mcp is not None:
            await refresh_mcp(force=True)
        for status in mcp_manager.status():
            if status.state == "connected":
                print_fn(f"{status.name}: connected · {len(status.tools)} tools")
            else:
                print_fn(f"{status.name}: failed · {status.error or 'unknown error'}")
        return None
    if command == "tools":
        print_fn(describe_tools(agent.tools))
        return None
    if command == "compact":
        try:
            path = await compact(agent)
        except Exception as exc:  # noqa: BLE001 - report, keep the REPL alive
            print_fn(f"compaction failed: {exc}")
        else:
            print_fn(f"compacted into {path.name}")
        return None
    if extension_commands and command in extension_commands:
        handler = extension_commands[command].handler
        result = handler(agent, argument)
        if inspect.isawaitable(result):
            result = await result
        if result:
            print_fn(str(result))
        return None
    print_fn(f"unknown command: /{command} (try /help)")
    return None


class _EventPrinter:
    """Streams assistant text and prints one line per tool result."""

    def __init__(
        self,
        console: Console | None = None,
        print_fn: PrintFn = print,
        theme: Theme | None = None,
    ) -> None:
        self.console = console
        self.print = print_fn
        self.theme = theme or DEFAULT_THEME
        self._line_open = False

    def __call__(self, event: AgentEvent) -> None:
        if event.type == "message_start":
            self._line_open = False
            return
        if event.type == "message_update" and event.assistant_event is not None:
            assistant_event = event.assistant_event
            if assistant_event.type == "text_delta" and assistant_event.delta:
                self.print(assistant_event.delta, end="")
                if self.print is print:
                    # stdout is line-buffered on a TTY (block-buffered on a
                    # pipe), so an unflushed delta would only surface when the
                    # closing newline arrives — the whole answer at once.
                    sys.stdout.flush()
                self._line_open = True
            elif assistant_event.type == "thinking_end" and self.console is not None:
                self._close_line()
                self.console.print("[grey50]· thought[/grey50]", highlight=False)
            return
        if event.type == "tool_execution_end":
            self._close_line()
            line = tool_result_line(
                event.tool_name or "?", event.result, event.duration_ms
            )
            if self.console is not None:
                self.console.print(line, highlight=False)
                diff = getattr(event.result, "details", {}).get("diff")
                if diff:
                    self.console.print(render_diff(diff, self.theme), highlight=False)
            else:
                self.print(line)
            return
        if event.type == "message_end":
            self._close_line()

    def _close_line(self) -> None:
        if self._line_open:
            self.print("")
            self._line_open = False


def _session_for(resume: str | Path | None) -> Session:
    """Open the resumed session, or start a fresh one."""
    if resume in (None, "", "latest"):
        path = latest_session() if resume == "latest" else None
        if path is None:
            path = unique_session_path()
    else:
        path = Path(resume)
    return Session(path)


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
    commands = {**SLASH_COMMANDS, **(extra_commands or {})}
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


async def repl(
    model_id: str | None = None,
    resume: str | Path | None = None,
    *,
    stream_fn: StreamFn | None = None,
    input_fn: InputFn | None = None,
    print_fn: PrintFn = print,
    api_key: str | None = None,
    tools: list | None = None,
    allow_dangerous: bool = False,
    max_turns: int | None = None,
    max_cost: float | None = None,
    retries: int = 0,
    context_files: bool = True,
    completion: bool = True,
    auto_compact: bool = True,
    trace: Path | str | None = None,
    mcp: bool = True,
    mcp_servers: list[str] | None = None,
    mcp_file: Path | str | None = None,
    mcp_retry_interval_s: float = 10.0,
    skills: bool = True,
    templates: bool = True,
    extension_paths: list[str] | None = None,
    read_only: bool = False,
    mode: str = "ask",
    theme: str | None = None,
    metrics: Path | None = None,
) -> int:
    """Run the interactive loop; returns the process exit code."""
    user_config = load_user_config()
    config_warnings: list[str] = []
    catalog = apply_user_config(load_catalog(), user_config, config_warnings)
    for warning in config_warnings:
        print(f"zenagent: {warning}", file=sys.stderr)
    try:
        model = resolve_model(catalog, model_id or user_config.default_model)
    except KeyError as exc:
        print(f"zenagent: unknown model: {exc}", file=sys.stderr)
        return 1
    key = api_key or _api_key_for(catalog, model)
    if not key:
        provider = catalog.get(model.provider)
        env_name = provider.api_key_env if provider else "the provider's API key"
        print(f"zenagent: missing API key for {model.id} (set {env_name})", file=sys.stderr)
        return 1

    model_values = [
        (
            candidate.id,
            f"{provider_id} · "
            + ("ready" if _api_key_for(catalog, candidate) else "no API key"),
        )
        for provider_id, provider in catalog.items()
        for candidate in provider.models
    ]

    session = _session_for(resume)
    tool_list = list(ALL_TOOLS if tools is None else tools)
    todo_tool = build_todo_tool(session.path) if tools is None else None
    if todo_tool is not None:
        tool_list.append(todo_tool)
    artifact_sink = build_artifact_sink(session.path)
    task_tool = (
        build_task_tool(
            model=lambda: agent.model,
            base_tools=list(ALL_TOOLS),
            api_key=lambda: agent.api_key,
            stream_fn=stream_fn,
            thinking=lambda: agent.thinking,
            max_cost=max_cost,
            # Read through the current sink so /fork, /compact and /import
            # retarget child artifacts too.
            artifact_sink=lambda tool_name, text: artifact_sink(tool_name, text),
        )
        if tools is None
        else None
    )
    if task_tool is not None:
        tool_list.append(task_tool)
    guard = ContextGuard(model.context_window)
    base_tools = list(tool_list)
    extension_files = [*user_config.extensions, *(extension_paths or [])]

    def warn(message: str) -> None:
        print(f"zenagent: {message}", file=sys.stderr)

    def build_prompt(current_tools, extra_text: str = "") -> str:
        text = _system_prompt([tool.name for tool in current_tools])
        if context_files:
            text = system_prompt_with_context(
                text, load_context_files(discover_context_files(Path.cwd()))
            )
        if extra_text:
            text = f"{text}\n\n{extra_text}"
        return text

    mcp_manager: McpManager | None = None
    if mcp and tools is None:
        configs = load_mcp_config(Path(mcp_file) if mcp_file else None)
        if mcp_servers:
            configs = {
                name: config for name, config in configs.items() if name in mcp_servers
            }
        if configs:
            mcp_manager = McpManager(configs, retry_interval_s=mcp_retry_interval_s)
            for warning in await mcp_manager.start():
                warn(f"mcp {warning}")

    def discover_runtime():
        """(tools, extra prompt, skills, templates, commands, hooks)"""
        found_tools = list(base_tools)
        if mcp_manager is not None:
            found_tools.extend(mcp_manager.tool_defs())
        prompt_parts: list[str] = []
        found_skills: list = []
        if skills and tools is None:
            found_skills, skill_warnings = discover_skills(
                default_skill_roots(Path.cwd())
            )
            for warning in skill_warnings:
                warn(f"skills {warning}")
            if found_skills:
                found_tools.append(build_skill_tool(found_skills))
                prompt_parts.append(skills_prompt_index(found_skills))
        found_templates: list = []
        if templates:
            found_templates, template_warnings = discover_templates(
                default_template_roots(Path.cwd())
            )
            for warning in template_warnings:
                warn(f"templates {warning}")
        found_commands: dict = {}
        found_hooks: dict = {}
        if extension_files:
            loaded = load_extensions(extension_files)
            for warning in loaded.warnings:
                warn(f"extension {warning}")
            found_tools.extend(loaded.tools)
            if loaded.prompt_text:
                prompt_parts.append(loaded.prompt_text)
            found_commands = loaded.commands
            found_hooks = loaded.hooks
        return (
            found_tools,
            "\n\n".join(prompt_parts),
            found_skills,
            found_templates,
            found_commands,
            found_hooks,
        )

    (
        tool_list,
        extra_text,
        skill_list,
        template_list,
        extension_commands,
        extension_hooks,
    ) = discover_runtime()
    template_by_name = {template.name: template for template in template_list}

    def typed_commands() -> list[tuple[str, str, str]]:
        """Slash entries with their type label; re-read after /reload."""
        entries = [
            (f"/{template.name}", template.description, "template")
            for template in template_list
        ]
        entries.extend(
            (f"/{command.name}", command.description, "extension")
            for command in extension_commands.values()
        )
        return entries

    gate = PermissionGate(
        interactive=True,
        allow_dangerous=allow_dangerous,
        allow=user_config.tool_allow,
        deny=user_config.tool_deny,
        mode=mode,
    )

    def system_prompt_for(base: str) -> str:
        note = mode_prompt_note(gate.mode)
        return f"{base}\n\n{note}" if note else base

    base_prompt = build_prompt(tool_list, extra_text)
    prompt = system_prompt_for(base_prompt)
    agent = Agent(
        model=model,
        system_prompt=prompt,
        tools=tool_list,
        api_key=key,
        thinking="high" if model.reasoning else "off",
        session_id=session_id_for(model, session.path.stem),
        stream_fn=stream_fn,
        transform_context=guard.transform,
        max_turns=max_turns,
        max_cost=max_cost,
        retries=retries,
        before_tool_call=gate.before_tool_call,
        artifact_sink=artifact_sink,
    )

    def set_mode(argument: str) -> str:
        requested = argument.strip()
        if not requested:
            return f"mode: {gate.mode} · {describe_modes()}"
        gate.set_mode(requested)  # ValueError drives the user-facing message
        agent.system_prompt = system_prompt_for(base_prompt)
        return f"mode: {gate.mode} · {describe_modes()}"
    if resume is not None:
        agent.replace_messages(session.load())
        for warning in session.warnings:
            print(f"zenagent: {warning}", file=sys.stderr)

    async def refresh_mcp(force: bool = False) -> None:
        """Reconnect failed servers and swap refreshed tools into the agent."""
        nonlocal base_prompt
        if mcp_manager is None:
            return
        for warning in await mcp_manager.retry_failed(force=force):
            print_fn(f"mcp: {warning}")
        refreshed = [*base_tools, *mcp_manager.tool_defs()]
        if [tool.name for tool in refreshed] != [tool.name for tool in agent.tools]:
            agent.tools = refreshed
            base_prompt = build_prompt(refreshed)
            agent.system_prompt = system_prompt_for(base_prompt)
            print_fn(f"mcp: tools updated ({len(refreshed)} available)")

    async def reload_runtime() -> None:
        """Re-discover skills, templates and extensions without restarting."""
        nonlocal tool_list, skill_list, template_list, template_by_name
        nonlocal extension_commands
        nonlocal extension_hooks, base_prompt
        if agent.is_streaming:
            print_fn("cannot reload while the agent is working")
            return
        (
            tools_now,
            extra_now,
            skills_now,
            templates_now,
            commands_now,
            hooks_now,
        ) = discover_runtime()
        tool_list, skill_list, template_list = tools_now, skills_now, templates_now
        template_by_name = {template.name: template for template in templates_now}
        extension_commands, extension_hooks = commands_now, hooks_now
        agent.tools = tools_now
        base_prompt = build_prompt(tools_now, extra_now)
        agent.system_prompt = system_prompt_for(base_prompt)
        print_fn(
            f"reloaded: {len(tools_now)} tools · {len(skills_now)} skills · "
            f"{len(templates_now)} templates"
        )

    console = Console() if sys.stdout.isatty() else None
    theme_warnings: list[str] = []
    try:
        active_theme = load_theme(theme, theme_warnings)
    except ValueError as exc:
        theme_warnings.append(str(exc))
        active_theme = DEFAULT_THEME
    for warning in theme_warnings:
        print(f"zenagent: theme {warning}", file=sys.stderr)
    keybinding_warnings: list[str] = []
    active_keybindings = load_keybindings(warnings=keybinding_warnings)
    for warning in keybinding_warnings:
        print(f"zenagent: {warning}", file=sys.stderr)
    printer = _EventPrinter(console=console, print_fn=print_fn, theme=active_theme)

    def set_theme(spec: str) -> None:
        nonlocal active_theme
        try:
            active_theme = load_theme(spec)
        except ValueError as exc:
            print_fn(str(exc))
            return
        printer.theme = active_theme
        print_fn(f"theme -> {active_theme.name}")

    session_box: list[Session] = [session]
    pending_images: list[ImageContent] = []

    def switch_session(new_session: Session) -> None:
        nonlocal artifact_sink
        session_box[0] = new_session
        # Artifacts belong to the session in context: retarget the spill sink
        # (and the task child's sink, which reads this variable).
        artifact_sink = build_artifact_sink(new_session.path)
        agent.artifact_sink = artifact_sink
        if todo_tool is not None:
            todo_tool.bind_session(new_session.path)

    def on_event(event: AgentEvent) -> None:
        if event.type == "agent_end" and event.new_messages:
            for message in event.new_messages:
                session_box[0].append(message)
        printer(event)

    metrics_recorder = MetricsRecorder(model.id) if metrics else None
    if metrics_recorder is not None:
        agent.subscribe(metrics_recorder)
    tracer = TraceRecorder(trace) if trace else None
    if tracer is not None:
        agent.subscribe(tracer)
    if extension_hooks:
        for event_type, callbacks in extension_hooks.items():
            def hook(event: AgentEvent, _type=event_type, _callbacks=callbacks) -> None:
                if event.type != _type:
                    return
                for callback in _callbacks:
                    try:
                        callback(event)
                    except Exception as exc:  # noqa: BLE001 - a hook must not kill the run
                        print(f"zenagent: extension hook failed: {exc}", file=sys.stderr)

            agent.subscribe(hook)
    agent.subscribe(guard.on_event)
    agent.subscribe(on_event)
    if console is not None:
        console.print(f"[grey50]{session.path.name}[/grey50] · /help for commands")

    def skill_provider() -> list[tuple[str, str]]:
        return [(skill.name, skill.description) for skill in skill_list]

    read = input_fn or _make_default_reader(
        completion=completion,
        keybindings=active_keybindings,
        extra_values={"/model": model_values},
        typed_provider=typed_commands,
        skill_provider=skill_provider,
    )
    run_task: asyncio.Task | None = None
    #: Only one reader may own stdin: the REPL's steering reader and the ask
    #: handler would otherwise steal each other's lines during a run.
    input_lock = asyncio.Lock()
    ask_active = asyncio.Event()

    async def ask_user(questions: list[dict]) -> list[dict]:
        """Answer an ``ask`` tool call from the terminal (blank = dismiss)."""
        ask_active.set()
        answers: list[dict] = []
        for index, question in enumerate(questions, start=1):
            title = question.get("header") or question.get("id") or f"question {index}"
            print_fn(f"\n[{index}/{len(questions)}] {title}: {question['question']}")
            options = question["options"]
            for position, option in enumerate(options, start=1):
                line = f"  {position}. {option['label']}"
                if option.get("description"):
                    line += f" — {option['description']}"
                if option["label"] == question.get("recommended"):
                    line += " (Recommended)"
                print_fn(line)
            print_fn(
                "answer with "
                + ("numbers, comma-separated" if question.get("multi") else "a number")
                + " · blank line = cancel"
            )
            while True:
                try:
                    async with input_lock:
                        raw = await read()
                except (EOFError, KeyboardInterrupt):
                    ask_active.clear()
                    return answers
                parts = [
                    part
                    for part in raw.replace(",", " ").split()
                    if part.strip()
                ]
                if not parts:
                    ask_active.clear()
                    return answers
                picks: list[str] = []
                for part in parts:
                    try:
                        value = int(part)
                    except ValueError:
                        continue
                    if 1 <= value <= len(options):
                        label = options[value - 1]["label"]
                        if label not in picks:
                            picks.append(label)
                if not question.get("multi"):
                    picks = picks[:1]
                if picks:
                    answers.append({"id": question["id"], "selected": picks})
                    break
                print_fn("invalid selection; try again")
        ask_active.clear()
        return answers

    async def reap() -> None:
        """Clear a finished run so the next line is treated as a new prompt."""
        nonlocal run_task, metrics_recorder
        if run_task is not None and run_task.done():
            await asyncio.gather(run_task, return_exceptions=True)
            run_task = None
            if auto_compact:
                compacted = await maybe_auto_compact(agent, print_fn=print_fn)
                if compacted is not None:
                    switch_session(Session(compacted))
            if metrics_recorder is not None:
                append_metrics(metrics, metrics_recorder.build(agent.termination))
                metrics_recorder = MetricsRecorder(model.id)
                agent.subscribe(metrics_recorder)
            print_fn(
                status_line(
                    model_id=agent.model.id,
                    thinking=agent.thinking,
                    messages=len(agent.messages),
                    usage=aggregate_usage(agent.messages),
                    context_window=agent.model.context_window,
                    session_name=session_box[0].path.name,
                )
            )

    set_ask_handler(ask_user)
    try:
        while True:
            await reap()
            if ask_active.is_set():
                # A pending ask (from inside the running agent) owns stdin.
                await ask_active.wait()
                continue
            try:
                async with input_lock:
                    if ask_active.is_set():
                        line = None
                    else:
                        line = await read()
            except (EOFError, KeyboardInterrupt):
                if run_task is not None:
                    agent.abort()
                    await asyncio.gather(run_task, return_exceptions=True)
                print_fn("")
                return 0
            if line is None:
                await ask_active.wait()
                continue

            await reap()  # the run may have finished while the user was typing
            if not line.strip():
                continue

            command, argument = split_input(line)
            prompt_text = line.strip()
            skill_prompt: str | None = None
            if command and command in template_by_name:
                prompt_text = expand(template_by_name[command].body, argument)
                command = ""
            resolution = build_skill_submission(
                line,
                skill_list,
                reserved_names={*template_by_name, *extension_commands},
            )
            if resolution is not None:
                if resolution.error is not None:
                    print_fn(resolution.error)
                    continue
                if resolution.submission is not None:
                    skill_prompt = resolution.submission.prompt
                    prompt_text = skill_prompt
                    command = ""
            if command == "retry":
                # Retry is a loop-level command: it truncates the session and
                # turns the recovered prompt into this iteration's input.
                prepared = (
                    None
                    if run_task is not None
                    else prepare_retry(agent, session_box[0])
                )
                if prepared is None:
                    print_fn("nothing to retry")
                    continue
                prompt_text, new_session = prepared
                switch_session(new_session)
                command = ""
            if command:
                if (
                    await handle_slash(
                        agent,
                        command,
                        argument,
                        print_fn=print_fn,
                        guard=guard,
                        session_path=session_box[0].path,
                        session=session_box[0],
                        switch_session=switch_session,
                        images=pending_images,
                        mcp_manager=mcp_manager,
                        refresh_mcp=refresh_mcp,
                        skill_list=skill_list,
                        skills_enabled=skills,
                        template_list=template_list,
                        extension_commands=extension_commands,
                        reload_runtime=reload_runtime,
                        set_theme=set_theme,
                        keybindings=active_keybindings,
                        set_mode=set_mode,
                    )
                    == "exit"
                ):
                    if run_task is not None:
                        agent.abort()
                        await asyncio.gather(run_task, return_exceptions=True)
                    return 0
                continue

            if run_task is not None:
                # A run is still going: this line steers it instead of stacking.
                agent.steer(
                    skill_prompt if skill_prompt is not None else line.strip()
                )
                continue

            await refresh_mcp()
            if pending_images:
                if not agent.model.supports_vision:
                    print_fn(
                        f"{agent.model.id} does not accept images; /images clear to drop them"
                    )
                    continue
                run_task = asyncio.create_task(
                    agent.prompt(prompt_text, images=list(pending_images))
                )
                pending_images.clear()
            else:
                run_task = asyncio.create_task(agent.prompt(prompt_text))
    finally:
        set_ask_handler(None)
        if run_task is not None:
            agent.abort()
            await asyncio.gather(run_task, return_exceptions=True)
        if tracer is not None:
            tracer.close()
        if mcp_manager is not None:
            await mcp_manager.close()
