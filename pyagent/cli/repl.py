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

from pyagent.agent import Agent
from pyagent.agent.session import (
    Session,
    latest_session,
    list_sessions,
    session_path,
    truncate_last_turn,
)
from pyagent.cli.compact import compact, maybe_auto_compact
from pyagent.cli.completion import SLASH_COMMANDS, ReplCompleter, help_text
from pyagent.cli.images import read_image_argument
from pyagent.cli.main import (
    _api_key_for,
    _system_prompt,
    resolve_model,
    session_id_for,
)
from pyagent.cli.permissions import PermissionGate
from pyagent.cli.render import (
    aggregate_usage,
    render_diff,
    status_line,
    tool_result_line,
)
from pyagent.config import ContextGuard, apply_user_config, load_user_config
from pyagent.context_files import (
    discover_context_files,
    load_context_files,
    system_prompt_with_context,
)
from pyagent.events import AgentEvent
from pyagent.extensions import load_extensions
from pyagent.mcp.config import load_mcp_config
from pyagent.mcp.manager import McpManager
from pyagent.providers.catalog import find_model, load_catalog
from pyagent.providers.registry import StreamFn
from pyagent.skills import (
    build_skill_tool,
    default_skill_roots,
    discover_skills,
    skills_prompt_index,
)
from pyagent.templates import default_template_roots, discover_templates, expand
from pyagent.tools import ALL_TOOLS
from pyagent.trace import TraceRecorder
from pyagent.types import ImageContent

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
    template_list=None,
    extension_commands=None,
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
        level = argument or "off"
        if level not in THINKING_LEVELS:
            print_fn("usage: /thinking off|low|medium|high|max")
            return None
        agent.thinking = level
        print_fn(f"thinking -> {level}")
        return None
    if command == "sessions":
        infos = list_sessions()
        if not infos:
            print_fn("no saved sessions")
            return None
        current = session.path if session is not None else None
        for position, info in enumerate(infos, start=1):
            mark = "*" if info.path == current else " "
            title = info.title or "(empty)"
            print_fn(f"{mark} {position:2d}. {info.path.name} · {info.messages} msgs · {title}")
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
    if command == "skills":
        if not skill_list:
            print_fn("no skills found (put SKILL.md files under .pyagent/skills)")
            return None
        for skill in skill_list:
            print_fn(f"- {skill.name}: {skill.description}")
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

    def __init__(self, console: Console | None = None, print_fn: PrintFn = print) -> None:
        self.console = console
        self.print = print_fn
        self._line_open = False

    def __call__(self, event: AgentEvent) -> None:
        if event.type == "message_start":
            self._line_open = False
            return
        if event.type == "message_update" and event.assistant_event is not None:
            assistant_event = event.assistant_event
            if assistant_event.type == "text_delta" and assistant_event.delta:
                self.print(assistant_event.delta, end="")
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
                    self.console.print(render_diff(diff), highlight=False)
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
            path = session_path()
    else:
        path = Path(resume)
    return Session(path)


def _make_default_reader(
    completion: bool = True, extra_commands: dict[str, str] | None = None
) -> InputFn:
    """prompt_toolkit reader with persistent history and completion."""
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import FileHistory

    history = Path.home() / ".pyagent" / "history"
    history.parent.mkdir(parents=True, exist_ok=True)
    commands = {**SLASH_COMMANDS, **(extra_commands or {})}
    session = PromptSession(
        history=FileHistory(str(history)),
        completer=ReplCompleter(slash_commands=commands) if completion else None,
    )

    async def read() -> str:
        return await asyncio.to_thread(session.prompt, "pyagent> ")

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
) -> int:
    """Run the interactive loop; returns the process exit code."""
    user_config = load_user_config()
    config_warnings: list[str] = []
    catalog = apply_user_config(load_catalog(), user_config, config_warnings)
    for warning in config_warnings:
        print(f"pyagent: {warning}", file=sys.stderr)
    try:
        model = resolve_model(catalog, model_id or user_config.default_model)
    except KeyError as exc:
        print(f"pyagent: unknown model: {exc}", file=sys.stderr)
        return 1
    key = api_key or _api_key_for(catalog, model)
    if not key:
        provider = catalog.get(model.provider)
        env_name = provider.api_key_env if provider else "the provider's API key"
        print(f"pyagent: missing API key for {model.id} (set {env_name})", file=sys.stderr)
        return 1

    tool_list = list(ALL_TOOLS if tools is None else tools)
    session = _session_for(resume)
    guard = ContextGuard(model.context_window)
    base_tools = list(tool_list)
    skills_index = ""
    extension_prompt = ""

    def build_prompt(current_tools) -> str:
        text = _system_prompt([tool.name for tool in current_tools])
        if context_files:
            text = system_prompt_with_context(
                text, load_context_files(discover_context_files(Path.cwd()))
            )
        extra = "\n\n".join(part for part in (skills_index, extension_prompt) if part)
        if extra:
            text = f"{text}\n\n{extra}"
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
                print(f"pyagent: mcp {warning}", file=sys.stderr)
            tool_list = [*tool_list, *mcp_manager.tool_defs()]

    skill_list = []
    if skills and tools is None:
        skill_list, skill_warnings = discover_skills(default_skill_roots(Path.cwd()))
        for warning in skill_warnings:
            print(f"pyagent: skills {warning}", file=sys.stderr)
        if skill_list:
            tool_list = [*tool_list, build_skill_tool(skill_list)]
            skills_index = skills_prompt_index(skill_list)

    template_list = []
    if templates:
        template_list, template_warnings = discover_templates(
            default_template_roots(Path.cwd())
        )
        for warning in template_warnings:
            print(f"pyagent: templates {warning}", file=sys.stderr)
    template_by_name = {template.name: template for template in template_list}
    extra_commands = {
        f"/{template.name}": template.description for template in template_list
    }

    extension_files = [*user_config.extensions, *(extension_paths or [])]
    loaded_extensions = load_extensions(extension_files) if extension_files else None
    extension_commands = {}
    if loaded_extensions is not None:
        for warning in loaded_extensions.warnings:
            print(f"pyagent: extension {warning}", file=sys.stderr)
        tool_list = [*tool_list, *loaded_extensions.tools]
        extension_prompt = loaded_extensions.prompt_text
        extension_commands = loaded_extensions.commands
        for command in extension_commands.values():
            extra_commands[f"/{command.name}"] = command.description

    prompt = build_prompt(tool_list)
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
        before_tool_call=PermissionGate(
            interactive=True, allow_dangerous=allow_dangerous
        ).before_tool_call,
    )
    if resume is not None:
        agent.replace_messages(session.load())
        for warning in session.warnings:
            print(f"pyagent: {warning}", file=sys.stderr)

    async def refresh_mcp(force: bool = False) -> None:
        """Reconnect failed servers and swap refreshed tools into the agent."""
        if mcp_manager is None:
            return
        for warning in await mcp_manager.retry_failed(force=force):
            print_fn(f"mcp: {warning}")
        refreshed = [*base_tools, *mcp_manager.tool_defs()]
        if [tool.name for tool in refreshed] != [tool.name for tool in agent.tools]:
            agent.tools = refreshed
            agent.system_prompt = build_prompt(refreshed)
            print_fn(f"mcp: tools updated ({len(refreshed)} available)")

    console = Console() if sys.stdout.isatty() else None
    printer = _EventPrinter(console=console, print_fn=print_fn)

    session_box: list[Session] = [session]
    pending_images: list[ImageContent] = []

    def switch_session(new_session: Session) -> None:
        session_box[0] = new_session

    def on_event(event: AgentEvent) -> None:
        if event.type == "agent_end" and event.new_messages:
            for message in event.new_messages:
                session_box[0].append(message)
        printer(event)

    tracer = TraceRecorder(trace) if trace else None
    if tracer is not None:
        agent.subscribe(tracer)
    if loaded_extensions is not None:
        for event_type, callbacks in loaded_extensions.hooks.items():
            def hook(event: AgentEvent, _type=event_type, _callbacks=callbacks) -> None:
                if event.type != _type:
                    return
                for callback in _callbacks:
                    try:
                        callback(event)
                    except Exception as exc:  # noqa: BLE001 - a hook must not kill the run
                        print(f"pyagent: extension hook failed: {exc}", file=sys.stderr)

            agent.subscribe(hook)
    agent.subscribe(guard.on_event)
    agent.subscribe(on_event)
    if console is not None:
        console.print(f"[grey50]{session.path.name}[/grey50] · /help for commands")

    read = input_fn or _make_default_reader(
        completion=completion, extra_commands=extra_commands
    )
    run_task: asyncio.Task | None = None

    async def reap() -> None:
        """Clear a finished run so the next line is treated as a new prompt."""
        nonlocal run_task
        if run_task is not None and run_task.done():
            await asyncio.gather(run_task, return_exceptions=True)
            run_task = None
            if auto_compact:
                compacted = await maybe_auto_compact(agent, print_fn=print_fn)
                if compacted is not None:
                    switch_session(Session(compacted))
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

    try:
        while True:
            await reap()
            try:
                line = await read()
            except (EOFError, KeyboardInterrupt):
                if run_task is not None:
                    agent.abort()
                    await asyncio.gather(run_task, return_exceptions=True)
                print_fn("")
                return 0

            await reap()  # the run may have finished while the user was typing
            if not line.strip():
                continue

            command, argument = split_input(line)
            prompt_text = line.strip()
            if command and command in template_by_name:
                prompt_text = expand(template_by_name[command].body, argument)
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
                        template_list=template_list,
                        extension_commands=extension_commands,
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
                agent.steer(line.strip())
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
        if run_task is not None:
            agent.abort()
            await asyncio.gather(run_task, return_exceptions=True)
        if tracer is not None:
            tracer.close()
        if mcp_manager is not None:
            await mcp_manager.close()
