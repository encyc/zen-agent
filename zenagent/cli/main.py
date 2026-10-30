"""Headless CLI: one prompt in, streamed plain text out, exit code out.

``zenagent -p "..."`` assembles an :class:`~zenagent.agent.agent.Agent`, prints the
assistant text as it streams (no ANSI, so it can be piped), summarises tool
results on one line each, and exits 0 on a clean run or 1 when the provider
failed. The interactive REPL lives in ``repl.py`` (Task 17).

Model resolution order: ``-m/--model``, then ``~/.zenagent/config.toml``'s
default (Task 21), then the catalog entry marked ``default: true``. The API key
comes from the environment variable named by the model's provider.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from zenagent import __version__
from zenagent.agent import Agent
from zenagent.agent.session import (
    Session,
    latest_session,
    list_sessions,
    unique_session_path,
)
from zenagent.app.compact import maybe_auto_compact
from zenagent.app.modes import MODES, mode_prompt_note, resolve_mode
from zenagent.app.support import (
    api_key_for as _api_key_for,
)
from zenagent.app.support import (
    resolve_model,
    session_id_for,
)
from zenagent.app.support import (
    system_prompt as _system_prompt,
)
from zenagent.attachments import read_image_argument
from zenagent.cli.doctor import run_doctor
from zenagent.cli.exportcmd import run_export
from zenagent.cli.permissions import PermissionGate
from zenagent.cli.usagecmd import run_usage
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
from zenagent.providers.catalog import Model, load_catalog
from zenagent.providers.registry import StreamFn
from zenagent.skills import (
    build_skill_tool,
    default_skill_roots,
    discover_skills,
    skills_prompt_index,
)
from zenagent.telemetry import MetricsRecorder, append_metrics
from zenagent.tools import ALL_TOOLS
from zenagent.trace import TraceRecorder
from zenagent.types import AssistantMessage, ImageContent, Message

__all__ = ["build_parser", "main", "resolve_model", "run_headless"]

THINKING_CHOICES = ("off", "low", "medium", "high", "max")


def _build_agent(
    model: Model,
    api_key: str,
    thinking: str | None,
    gate: PermissionGate,
    stream_fn: StreamFn | None = None,
    tools: list | None = None,
    session_id: str | None = None,
    transform_context=None,
    max_turns: int | None = None,
    max_cost: float | None = None,
    retries: int = 0,
    context_files: bool = True,
    extra_prompt: str = "",
) -> Agent:
    """Construct the run's Agent (a seam for tests to inject a fake stream)."""
    tool_list = list(ALL_TOOLS if tools is None else tools)
    prompt = _system_prompt([tool.name for tool in tool_list])
    if context_files:
        prompt = system_prompt_with_context(
            prompt, load_context_files(discover_context_files(Path.cwd()))
        )
    if extra_prompt:
        prompt = f"{prompt}\n\n{extra_prompt}"
    return Agent(
        model=model,
        system_prompt=prompt,
        tools=tool_list,
        api_key=api_key,
        thinking=thinking,
        session_id=session_id_for(model, session_id),
        stream_fn=stream_fn,
        before_tool_call=gate.before_tool_call,
        transform_context=transform_context,
        max_turns=max_turns,
        max_cost=max_cost,
        retries=retries,
    )


@dataclass
class _PlainOutput:
    """Streams assistant text to stdout and prints one line per tool result."""

    out: object = None  # type: ignore[assignment]
    collected: list[str] = field(default_factory=list)
    _wrote_text: bool = False

    def __post_init__(self) -> None:
        if self.out is None:  # resolved at construction, not import time
            self.out = sys.stdout

    def __call__(self, event: AgentEvent) -> None:
        if event.type == "message_start":
            self._wrote_text = False
            return
        if event.type == "message_update" and event.assistant_event is not None:
            assistant_event = event.assistant_event
            if assistant_event.type == "text_delta" and assistant_event.delta:
                self.collected.append(assistant_event.delta)
                self.out.write(assistant_event.delta)
                self.out.flush()
                self._wrote_text = True
            return
        if event.type == "tool_execution_end":
            status = "error" if event.is_error else "ok"
            self._newline_if_needed()
            self.out.write(f"[{event.tool_name}: {status}]\n")
            self.out.flush()
            return
        if event.type == "message_end" and isinstance(event.message, AssistantMessage):
            self._newline_if_needed()
            self.out.flush()

    def _newline_if_needed(self) -> None:
        """Terminate a streamed line, but never emit blank spacer lines."""
        if self._wrote_text:
            self.out.write("\n")
            self._wrote_text = False

    @property
    def text(self) -> str:
        return "".join(self.collected).strip()


def _usage_and_counts(messages: list[Message]) -> tuple[dict, int]:
    usage: dict = {
        "input": 0,
        "output": 0,
        "cache_read": 0,
        "cache_write": 0,
        "cost": 0.0,
    }
    for message in messages:
        if isinstance(message, AssistantMessage):
            usage["input"] += message.usage.input
            usage["output"] += message.usage.output
            usage["cache_read"] += message.usage.cache_read
            usage["cache_write"] += message.usage.cache_write
            usage["cost"] += message.usage.cost.total
    return usage, len(messages)


async def run_headless(
    prompt: str,
    model_id: str | None = None,
    assume_yes: bool = False,
    thinking: str | None = None,
    *,
    allow_dangerous: bool = False,
    json_output: bool = False,
    max_turns: int | None = None,
    max_cost: float | None = None,
    retries: int = 0,
    context_files: bool = True,
    metrics: Path | None = None,
    stream_fn: StreamFn | None = None,
    tools: list | None = None,
    resume: str | Path | None = None,
    auto_compact: bool = True,
    images: list[Path] | None = None,
    trace: Path | str | None = None,
    mcp: bool = True,
    mcp_servers: list[str] | None = None,
    mcp_file: Path | str | None = None,
    skills: bool = True,
    extension_paths: list[str] | None = None,
    read_only: bool = False,
    mode: str | None = None,
) -> int:
    """Run one prompt to completion; returns the process exit code."""
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
    api_key = _api_key_for(catalog, model)
    if not api_key:
        provider = catalog.get(model.provider)
        env_name = provider.api_key_env if provider else "the provider's API key"
        print(f"zenagent: missing API key for {model.id} (set {env_name})", file=sys.stderr)
        return 1

    image_blocks: list[ImageContent] = []
    for image_path in images or []:
        if not model.supports_vision:
            print(
                f"zenagent: {model.id} does not accept images "
                "(pick a vision-capable model)",
                file=sys.stderr,
            )
            return 1
        try:
            image_blocks.append(read_image_argument(image_path))
        except ValueError as exc:
            print(f"zenagent: {exc}", file=sys.stderr)
            return 1

    if thinking is None:
        thinking = "high" if model.reasoning else "off"
    resolved_mode = resolve_mode(
        explicit=mode, assume_yes=assume_yes, read_only=read_only
    )
    gate = PermissionGate(
        assume_yes=assume_yes,
        interactive=False,
        allow_dangerous=allow_dangerous,
        allow=user_config.tool_allow,
        deny=user_config.tool_deny,
        mode=resolved_mode,
    )
    tool_list = list(ALL_TOOLS if tools is None else tools)
    mcp_manager: McpManager | None = None
    if mcp and tools is None:
        configs = load_mcp_config(Path(mcp_file) if mcp_file else None)
        if mcp_servers:
            configs = {
                name: config for name, config in configs.items() if name in mcp_servers
            }
        if configs:
            mcp_manager = McpManager(configs)
            for warning in await mcp_manager.start():
                print(f"zenagent: mcp {warning}", file=sys.stderr)
            tool_list = [*tool_list, *mcp_manager.tool_defs()]

    skills_index = ""
    if skills and tools is None:
        discovered, skill_warnings = discover_skills(default_skill_roots(Path.cwd()))
        for warning in skill_warnings:
            print(f"zenagent: skills {warning}", file=sys.stderr)
        if discovered:
            tool_list = [*tool_list, build_skill_tool(discovered)]
            skills_index = skills_prompt_index(discovered)

    extension_prompt = ""
    loaded_extensions = None
    extension_files = [*user_config.extensions, *(extension_paths or [])]
    if extension_files:
        loaded_extensions = load_extensions(extension_files)
        for warning in loaded_extensions.warnings:
            print(f"zenagent: extension {warning}", file=sys.stderr)
        if loaded_extensions.tools:
            tool_list = [*tool_list, *loaded_extensions.tools]
        extension_prompt = loaded_extensions.prompt_text

    extra_prompt = "\n\n".join(
        part
        for part in (skills_index, extension_prompt, mode_prompt_note(resolved_mode))
        if part
    )
    guard = ContextGuard(model.context_window)
    agent = _build_agent(
        model,
        api_key,
        thinking,
        gate,
        stream_fn=stream_fn,
        tools=tool_list,
        transform_context=guard.transform,
        max_turns=max_turns,
        max_cost=max_cost,
        retries=retries,
        context_files=context_files,
        extra_prompt=extra_prompt,
    )

    if resume is not None:
        path = latest_session() if resume == "latest" else Path(resume)
        if path is not None and path.is_file():
            session = Session(path)
            agent.replace_messages(session.load())
            for warning in session.warnings:
                print(f"zenagent: {warning}", file=sys.stderr)
        else:
            print(f"zenagent: no session to resume ({resume})", file=sys.stderr)
            session = Session(unique_session_path())
    else:
        session = Session(unique_session_path())

    def persist(event: AgentEvent) -> None:
        if event.type == "agent_end" and event.new_messages:
            for message in event.new_messages:
                session.append(message)

    # --json must not stream plain text; collect it into a buffer instead.
    printer = _PlainOutput(out=io.StringIO() if json_output else None)
    if loaded_extensions is not None:
        for event_type, callbacks in loaded_extensions.hooks.items():
            def hook(event: AgentEvent, _type=event_type, _callbacks=callbacks) -> None:
                if event.type != _type:
                    return
                for callback in _callbacks:
                    try:
                        callback(event)
                    except Exception as exc:  # noqa: BLE001 - a hook must not kill the run
                        print(f"zenagent: extension hook failed: {exc}", file=sys.stderr)

            agent.subscribe(hook)

    metrics_recorder = MetricsRecorder(model.id) if metrics else None
    if metrics_recorder is not None:
        agent.subscribe(metrics_recorder)
    tracer = TraceRecorder(trace) if trace else None
    if tracer is not None:
        agent.subscribe(tracer)
    agent.subscribe(guard.on_event)
    agent.subscribe(persist)
    agent.subscribe(printer)
    try:
        await agent.prompt(prompt, images=image_blocks or None)
        if auto_compact:
            await maybe_auto_compact(
                agent, print_fn=lambda message: print(message, file=sys.stderr)
            )
    finally:
        if mcp_manager is not None:
            await mcp_manager.close()
        if metrics_recorder is not None:
            append_metrics(metrics, metrics_recorder.build(agent.termination))

    ok = agent.error_message is None and agent.termination in ("stop", "tool_terminate")
    if json_output:
        usage, count = _usage_and_counts(agent.messages)
        print(
            json.dumps(
                {
                    "ok": ok,
                    "text": printer.text,
                    "usage": usage,
                    "messages": count,
                    "error": agent.error_message,
                    "termination": agent.termination,
                },
                ensure_ascii=False,
            )
        )
    elif agent.error_message is not None:
        print(f"zenagent: {agent.error_message}", file=sys.stderr)
    elif not ok:
        print(
            f"zenagent: run stopped early ({agent.termination})",
            file=sys.stderr,
        )
    if tracer is not None:
        tracer.close()
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="zenagent", description="A minimal coding agent (streaming, tool-using)."
    )
    parser.add_argument(
        "command",
        nargs="?",
        help="optional subcommand: doctor, sessions",
    )
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"zenagent {__version__}",
        help="print the version and exit",
    )
    parser.add_argument("-p", "--prompt", help="run one prompt headlessly and exit")
    parser.add_argument("session", nargs="?", help="session path for: export")
    parser.add_argument(
        "--format", choices=("md", "html"), default="md", help="export format (default md)"
    )
    parser.add_argument(
        "--since", metavar="YYYY-MM-DD", help="usage: only newer sessions"
    )
    parser.add_argument(
        "-o", "--output", type=Path, help="export output path (default: next to the session)"
    )
    parser.add_argument("-m", "--model", help="model id from the catalog")
    parser.add_argument(
        "--thinking",
        choices=THINKING_CHOICES,
        help="reasoning level (default: high for reasoning models, off otherwise; max when supported)",
    )
    parser.add_argument(
        "--yes", action="store_true", help="allow write/execute tools without asking"
    )
    parser.add_argument(
        "--read-only",
        action="store_true",
        help="refuse every tool that can modify the filesystem or run commands",
    )
    parser.add_argument(
        "--mode",
        choices=MODES,
        default=None,
        help="runtime permission mode: plan (read-only) | ask (confirm) | full (automatic)",
    )
    parser.add_argument(
        "--allow-dangerous",
        action="store_true",
        help="also allow catastrophic shell commands (rm -rf /, mkfs, ...)",
    )
    parser.add_argument(
        "--resume", nargs="?", const="latest", help="resume a session (Task 15)"
    )
    parser.add_argument("--json", action="store_true", help="emit a JSON result")
    parser.add_argument(
        "--image",
        action="append",
        dest="images",
        metavar="PATH",
        help="attach an image (repeatable; needs a vision-capable model)",
    )
    parser.add_argument(
        "--metrics",
        metavar="PATH",
        help="append one JSON metrics line per run",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="with doctor: print a redacted diagnostics report",
    )
    parser.add_argument(
        "--report-out",
        type=Path,
        help="with doctor --report: write the report to this file",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=0,
        help=(
            "extra attempts for transient provider failures; each attempt is "
            "buffered, so enabling retries disables token streaming (default 0)"
        ),
    )
    parser.add_argument(
        "--max-turns", type=int, help="stop after this many provider requests"
    )
    parser.add_argument(
        "--theme",
        metavar="NAME",
        help="repl colour theme: default|mono|solarized|<path.json>",
    )
    parser.add_argument(
        "--extension",
        action="append",
        dest="extension_paths",
        metavar="PATH",
        help="load an extension module (repeatable)",
    )
    parser.add_argument(
        "--no-templates",
        action="store_true",
        help="do not load prompt templates from .zenagent/commands",
    )
    parser.add_argument(
        "--no-skills",
        action="store_true",
        help="do not load skills from .zenagent/skills",
    )
    parser.add_argument(
        "--no-mcp",
        action="store_true",
        help="do not connect any MCP servers",
    )
    parser.add_argument(
        "--mcp-server",
        action="append",
        dest="mcp_servers",
        metavar="NAME",
        help="only connect this MCP server (repeatable)",
    )
    parser.add_argument(
        "--mcp-file",
        metavar="PATH",
        help="MCP config file (default: ~/.zenagent/mcp.json)",
    )
    parser.add_argument(
        "--cwd",
        metavar="PATH",
        help="working directory to run in (default: current directory)",
    )
    parser.add_argument(
        "--trace",
        metavar="PATH",
        help="append a JSONL trace of turns and tool calls",
    )
    parser.add_argument(
        "--list-sessions",
        action="store_true",
        help="list this project's saved sessions and exit",
    )
    parser.add_argument(
        "--all-projects",
        action="store_true",
        help="with --list-sessions: include every project and legacy sessions",
    )
    parser.add_argument(
        "--no-auto-compact",
        action="store_true",
        help="never compact automatically when the context gets full",
    )
    parser.add_argument(
        "--no-tui",
        action="store_true",
        help="use the line-based REPL even in a terminal",
    )
    parser.add_argument(
        "--no-completion",
        action="store_true",
        help="disable slash-command and @path completion in the repl",
    )
    parser.add_argument(
        "--no-context-files",
        action="store_true",
        help="do not load AGENTS.md / CLAUDE.md into the system prompt",
    )
    parser.add_argument(
        "--max-cost", type=float, help="stop when the run has spent this many USD"
    )
    return parser


def _tui_app_class():
    """Import the Textual app lazily so a missing extra is a clear error."""
    from zenagent.cli.tui.app import ZenAgentApp

    return ZenAgentApp


_CLI_EXTRAS_HINT = (
    "zenagent: the {frontend} needs the CLI extras ({missing} is missing).\n"
    "  in the repo:  uv sync --extra cli\n"
    '  from PyPI:    uv tool install --force "zenagent-py[cli]"'
)


def _missing_extra(*modules: str) -> str | None:
    """The first CLI-extra dependency that is not installed, if any.

    Checked before importing a frontend so a kernel-only install
    (``pip install zenagent-py``) gets a hint instead of a traceback.
    """
    import importlib.util

    for module in modules:
        if importlib.util.find_spec(module) is None:
            return module
    return None


def _missing_extras(frontend: str, missing: str) -> int:
    print(_CLI_EXTRAS_HINT.format(frontend=frontend, missing=missing), file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    raw = sys.argv[1:] if argv is None else list(argv)
    if raw and raw[0] == "rpc":
        from zenagent.cli.rpc import main as rpc_main

        return rpc_main(raw[1:])
    if raw and raw[0] == "sessions":
        from zenagent.cli.sessionscmd import main as sessions_main

        return sessions_main(raw[1:])
    if raw and raw[0] == "evals":
        import importlib.util

        script = Path(__file__).resolve().parents[2] / "scripts" / "eval.py"
        spec = importlib.util.spec_from_file_location("zenagent_eval_script", script)
        if spec is None or spec.loader is None:
            print("zenagent: cannot load the eval runner", file=sys.stderr)
            return 2
        module = importlib.util.module_from_spec(spec)
        # dataclasses resolves string annotations through sys.modules, so the
        # module must be registered before exec_module runs.
        sys.modules[spec.name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(spec.name, None)
            raise
        return module.main(raw[1:])
    args = build_parser().parse_args(argv)
    mode = resolve_mode(
        explicit=args.mode, assume_yes=args.yes, read_only=args.read_only
    )
    if args.cwd:
        target = Path(args.cwd).expanduser()
        if not target.is_dir():
            print(f"zenagent: --cwd is not a directory: {target}", file=sys.stderr)
            return 1
        os.chdir(target.resolve())
    if args.command == "usage":
        return run_usage(args.since, args.json)
    if args.command == "export":
        return run_export(args.session, args.format, args.output)
    if args.command == "doctor":
        if args.report:
            from zenagent.crash import collect_report

            report = collect_report()
            if args.report_out:
                args.report_out.write_text(report, encoding="utf-8")
                print(f"zenagent: report written to {args.report_out}")
            else:
                print(report, end="")
            return 0
        return asyncio.run(run_doctor())
    if args.command is not None:
        print(f"zenagent: unknown command: {args.command} (try: doctor)", file=sys.stderr)
        return 1
    if args.list_sessions:
        scope = "all" if args.all_projects else "project"
        infos = list_sessions(scope=scope)
        if not infos:
            print("no saved sessions")
            if scope == "project":
                print("hint: --all-projects lists every project")
            return 0
        for position, info in enumerate(infos, start=1):
            label = ""
            if scope == "all":
                label = f"[{info.project or 'legacy'}] "
            print(
                f"{position:2d}. {label}{info.title or '(empty)'} · "
                f"{info.messages} msgs · {info.path.name}"
            )
        return 0
    if not args.prompt:
        if sys.stdout.isatty() and not args.no_tui:
            from zenagent.app.permissions import PermissionBroker, PermissionGate
            from zenagent.app.runtime import Runtime, RuntimeOptions

            missing = _missing_extra("textual", "rich")
            if missing:
                return _missing_extras("TUI", missing)

            options = RuntimeOptions(
                model_id=args.model,
                resume=args.resume,
                mcp=not args.no_mcp,
                mcp_servers=args.mcp_servers,
                mcp_file=args.mcp_file,
                skills=not args.no_skills,
                templates=not args.no_templates,
                extension_paths=args.extension_paths or [],
                context_files=not args.no_context_files,
                read_only=args.read_only,
                allow_dangerous=args.allow_dangerous,
                mode=mode,
                retries=args.retries,
                max_turns=args.max_turns,
                max_cost=args.max_cost,
            )

            tui_config = load_user_config()
            broker = PermissionBroker.wrap(
                PermissionGate(
                    assume_yes=False,
                    interactive=False,
                    allow_dangerous=args.allow_dangerous,
                    allow=tui_config.tool_allow,
                    deny=tui_config.tool_deny,
                    mode=mode,
                )
            )

            async def run_tui_session() -> int:
                # Imported here so a broken install is reported by the except
                # below instead of escaping as a traceback.
                ZenAgentApp = _tui_app_class()
                runtime = await Runtime.bootstrap(options, gate=broker)
                try:
                    app = ZenAgentApp(runtime)
                    broker.set_confirm(app.request_tool_approval)
                    await app.run_async()
                    if app.exit_summary:
                        print(app.exit_summary)
                    return 0
                finally:
                    await runtime.close()

            try:
                return asyncio.run(run_tui_session())
            except (RuntimeError, KeyError, ImportError) as exc:
                print(f"zenagent: cannot start the TUI: {exc}", file=sys.stderr)
                return 2

        missing = _missing_extra("rich", "prompt_toolkit")
        if missing:
            return _missing_extras("REPL", missing)

        # Line-based REPL, imported lazily to avoid a cycle.
        from zenagent.cli.repl import repl

        try:
            return asyncio.run(
                repl(
                    model_id=args.model,
                    resume=args.resume,
                    allow_dangerous=args.allow_dangerous,
                    context_files=not args.no_context_files,
                    completion=not args.no_completion,
                    auto_compact=not args.no_auto_compact,
                    trace=args.trace,
                    mcp=not args.no_mcp,
                    mcp_servers=args.mcp_servers,
                    mcp_file=args.mcp_file,
                    skills=not args.no_skills,
                    extension_paths=args.extension_paths,
                    read_only=args.read_only,
                    mode=mode,
                    theme=args.theme,
                    templates=not args.no_templates,
                )
            )
        except KeyboardInterrupt:
            print(file=sys.stderr)
            return 1
    try:
        return asyncio.run(
            run_headless(
                args.prompt,
                model_id=args.model,
                assume_yes=args.yes,
                thinking=args.thinking,
                allow_dangerous=args.allow_dangerous,
                json_output=args.json,
                resume=args.resume,
                max_turns=args.max_turns,
                max_cost=args.max_cost,
                retries=args.retries,
                metrics=args.metrics,
                context_files=not args.no_context_files,
                auto_compact=not args.no_auto_compact,
                images=args.images,
                trace=args.trace,
                mcp=not args.no_mcp,
                mcp_servers=args.mcp_servers,
                mcp_file=args.mcp_file,
                skills=not args.no_skills,
                extension_paths=args.extension_paths,
                read_only=args.read_only,
                mode=mode,
            )
        )
    except KeyboardInterrupt:
        print("\nzenagent: interrupted", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
