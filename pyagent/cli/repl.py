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
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

from rich.console import Console

from pyagent.agent import Agent
from pyagent.agent.session import Session, latest_session, session_path
from pyagent.cli.compact import compact
from pyagent.cli.main import _api_key_for, _system_prompt, resolve_model, session_id_for
from pyagent.cli.permissions import PermissionGate
from pyagent.cli.render import tool_result_line
from pyagent.events import AgentEvent
from pyagent.providers.catalog import find_model, load_catalog
from pyagent.providers.registry import StreamFn
from pyagent.tools import ALL_TOOLS

__all__ = [
    "HELP_TEXT",
    "THINKING_LEVELS",
    "handle_slash",
    "repl",
    "split_input",
]

InputFn = Callable[[], Awaitable[str]]
PrintFn = Callable[..., None]

THINKING_LEVELS = ("off", "low", "medium", "high")

HELP_TEXT = """commands:
  /help                 show this help
  /exit                 leave the REPL (Ctrl+D also works)
  /clear                start over with an empty transcript
  /model [id]           show or switch the model (takes effect next turn)
  /thinking [level]     show or set reasoning level: off|low|medium|high
  /compact              summarize the conversation into a fresh session
while the agent is working, any line you type is queued as steering input."""


def split_input(line: str) -> tuple[str, str]:
    """``("", text)`` for a prompt, or ``(command, argument)`` for ``/command``."""
    stripped = line.strip()
    if stripped.startswith("/"):
        command, _, argument = stripped[1:].partition(" ")
        return command.lower(), argument.strip()
    return "", stripped


async def handle_slash(
    agent: Agent, command: str, argument: str, *, print_fn: PrintFn = print
) -> str | None:
    """Run one slash command; returns ``"exit"`` when the REPL should stop."""
    if command in ("exit", "quit"):
        return "exit"
    if command in ("help", "?"):
        print_fn(HELP_TEXT)
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
            print_fn(f"model -> {agent.model.id} (next turn)")
        return None
    if command == "thinking":
        level = argument or "off"
        if level not in THINKING_LEVELS:
            print_fn("usage: /thinking off|low|medium|high")
            return None
        agent.thinking = level
        print_fn(f"thinking -> {level}")
        return None
    if command == "compact":
        try:
            path = await compact(agent)
        except Exception as exc:  # noqa: BLE001 - report, keep the REPL alive
            print_fn(f"compaction failed: {exc}")
        else:
            print_fn(f"compacted into {path.name}")
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


def _make_default_reader() -> InputFn:
    """prompt_toolkit reader with persistent history."""
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import FileHistory

    history = Path.home() / ".pyagent" / "history"
    history.parent.mkdir(parents=True, exist_ok=True)
    session = PromptSession(history=FileHistory(str(history)))

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
) -> int:
    """Run the interactive loop; returns the process exit code."""
    catalog = load_catalog()
    try:
        model = resolve_model(catalog, model_id)
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
    agent = Agent(
        model=model,
        system_prompt=_system_prompt([tool.name for tool in tool_list]),
        tools=tool_list,
        api_key=key,
        thinking="high" if model.reasoning else "off",
        session_id=session_id_for(model, session.path.stem),
        stream_fn=stream_fn,
        before_tool_call=PermissionGate(interactive=True).before_tool_call,
    )
    if resume is not None:
        agent.replace_messages(session.load())
        for warning in session.warnings:
            print(f"pyagent: {warning}", file=sys.stderr)

    console = Console() if sys.stdout.isatty() else None
    printer = _EventPrinter(console=console, print_fn=print_fn)

    def on_event(event: AgentEvent) -> None:
        if event.type == "agent_end" and event.new_messages:
            for message in event.new_messages:
                session.append(message)
        printer(event)

    agent.subscribe(on_event)
    if console is not None:
        console.print(f"[grey50]{session.path.name}[/grey50] · /help for commands")

    read = input_fn or _make_default_reader()
    run_task: asyncio.Task | None = None

    async def reap() -> None:
        """Clear a finished run so the next line is treated as a new prompt."""
        nonlocal run_task
        if run_task is not None and run_task.done():
            await asyncio.gather(run_task, return_exceptions=True)
            run_task = None

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
            if command:
                if await handle_slash(agent, command, argument, print_fn=print_fn) == "exit":
                    if run_task is not None:
                        agent.abort()
                        await asyncio.gather(run_task, return_exceptions=True)
                    return 0
                continue

            if run_task is not None:
                # A run is still going: this line steers it instead of stacking.
                agent.steer(line.strip())
                continue

            run_task = asyncio.create_task(agent.prompt(line.strip()))
    finally:
        if run_task is not None:
            agent.abort()
            await asyncio.gather(run_task, return_exceptions=True)
