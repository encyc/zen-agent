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
import contextlib
import io
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from zenagent import __version__
from zenagent.agent.session import list_sessions
from zenagent.app.compact import maybe_auto_compact
from zenagent.app.modes import MODES, resolve_mode
from zenagent.app.permissions import build_broker_for
from zenagent.app.runtime import Runtime, RuntimeOptions
from zenagent.app.support import resolve_model
from zenagent.attachments import read_image_argument
from zenagent.cli.doctor import run_doctor
from zenagent.cli.exportcmd import run_export
from zenagent.cli.usagecmd import run_usage
from zenagent.config import load_user_config
from zenagent.events import AgentEvent
from zenagent.i18n import LANGUAGE_CHOICES
from zenagent.providers.registry import StreamFn
from zenagent.types import AssistantMessage, ImageContent, Message

__all__ = ["build_parser", "main", "resolve_model", "run_headless"]

THINKING_CHOICES = ("off", "low", "medium", "high", "max")


#: The assembly seam: headless delegates to ``Runtime.bootstrap`` — the same
#: catalog → policy → gate → tools → session wiring the TUI, REPL and RPC use —
#: instead of re-wiring them here. Tests wrap this alias to inject a fake
#: stream and capture the built runtime.
_bootstrap = Runtime.bootstrap


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
        # ``tool_execution_update`` (streamed partial output) is deliberately
        # ignored here: headless output is one scriptable transcript, and
        # in-place progress lines would corrupt a pipe/redirected capture.
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


def _read_images(model, images) -> list[ImageContent] | None:
    """Load ``--image`` paths; None (after a stderr line) means the run cannot start."""
    blocks: list[ImageContent] = []
    for image_path in images or []:
        if not model.supports_vision:
            print(
                f"zenagent: {model.id} does not accept images "
                "(pick a vision-capable model)",
                file=sys.stderr,
            )
            return None
        try:
            blocks.append(read_image_argument(Path(image_path)))
        except ValueError as exc:
            print(f"zenagent: {exc}", file=sys.stderr)
            return None
    return blocks


async def run_headless(
    prompt: str,
    model_id: str | None = None,
    assume_yes: bool = False,
    thinking: str | None = None,
    *,
    allow_dangerous: bool = False,
    allow_outside: list[str] | None = None,
    allow_destructive: bool = False,
    allow_protected: bool = False,
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
    """Run one prompt to completion; returns the process exit code.

    Assembly is delegated to :meth:`Runtime.bootstrap`, so the one-shot run
    shares the Runtime's policy, tool registry, memory, subagents and session
    wiring (and its catalog/model resolution, which raises ``KeyError`` for an
    unknown model and ``RuntimeError`` for a missing key). This wrapper keeps
    only the headless output contract: image loading, streaming/JSON printing,
    post-run auto-compaction, stderr diagnostics and the exit code.
    """
    # A typo'd image path fails before assembly: bootstrapping first would
    # leave an empty session file behind, which shows up in --list-sessions.
    for image_path in images or []:
        if not Path(image_path).is_file():
            print(
                f"zenagent: cannot read image {image_path}: no such file",
                file=sys.stderr,
            )
            return 1

    options = RuntimeOptions(
        model_id=model_id,
        thinking=thinking,
        resume=resume,
        mcp=mcp,
        mcp_file=Path(mcp_file) if mcp_file else None,
        mcp_servers=mcp_servers,
        skills=skills,
        # Nothing in a one-shot run expands prompt templates (they are a
        # slash-command surface), so discovery is skipped like before.
        templates=False,
        extension_paths=list(extension_paths or []),
        context_files=context_files,
        read_only=read_only,
        allow_dangerous=allow_dangerous,
        allow_outside=list(allow_outside or []),
        allow_destructive=allow_destructive,
        allow_protected=allow_protected,
        mode=mode,
        retries=retries,
        max_turns=max_turns,
        max_cost=max_cost,
        trace=trace,
        metrics=Path(metrics) if metrics else None,
        auto_compact=auto_compact,
        tools=tools,
    )
    try:
        runtime = await _bootstrap(
            options, stream_fn=stream_fn, assume_yes=assume_yes, allow_missing_key=False
        )
    except KeyError as exc:
        print(f"zenagent: unknown model: {exc}", file=sys.stderr)
        return 1
    except (RuntimeError, LookupError) as exc:
        print(f"zenagent: {exc}", file=sys.stderr)
        return 1
    for warning in runtime.warnings:
        print(f"zenagent: {warning}", file=sys.stderr)

    agent = runtime.agent
    image_blocks = _read_images(agent.model, images)
    if image_blocks is None:
        await runtime.close()
        return 1

    # --json must not stream plain text; collect it into a buffer instead.
    printer = _PlainOutput(out=io.StringIO() if json_output else None)
    agent.subscribe(printer)
    try:
        await agent.prompt(prompt, images=image_blocks or None)
        if auto_compact:
            await maybe_auto_compact(
                agent, print_fn=lambda message: print(message, file=sys.stderr)
            )
    finally:
        # Runtime.close releases MCP, the cached LSP clients and this run's
        # background bash jobs, closes the tracer and flushes metrics. Teardown
        # must never turn a finished run into a failure.
        with contextlib.suppress(Exception):
            await runtime.close()

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
        "--yes", action="store_true", help="run everything without asking (zen mode)"
    )
    parser.add_argument(
        "--read-only",
        action="store_true",
        help="refuse every tool that can modify the filesystem or run commands",
    )
    parser.add_argument(
        "--lang",
        choices=LANGUAGE_CHOICES,
        default=None,
        help="interface language: auto (system) | en | zh",
    )
    parser.add_argument(
        "--mode",
        choices=MODES,
        default=None,
        help="runtime permission mode: plan (read-only) | ask (confirm) | "
        "zen (fully automatic, no confirmations)",
    )
    parser.add_argument(
        "--allow-dangerous",
        action="store_true",
        help="also allow catastrophic shell commands (rm -rf /, mkfs, ...)",
    )
    parser.add_argument(
        "--allow-outside",
        action="append",
        dest="allow_outside",
        metavar="PATH",
        help="allow writes outside the workspace under this path (repeatable)",
    )
    parser.add_argument(
        "--allow-destructive",
        action="store_true",
        help="allow destructive shell commands (git reset --hard, ...)",
    )
    parser.add_argument(
        "--allow-protected",
        action="store_true",
        help="allow writes to protected credential paths",
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
        help="colour theme: auto|dark|light (repl also accepts default|mono|solarized|<path.json>)",
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
    if raw and raw[0] == "prompts":
        from zenagent.cli.prompts_cmd import main as prompts_main

        return prompts_main(raw[1:])
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
            from zenagent.cli.doctor import collect_report

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
        if args.prompt:
            print(
                "zenagent: --list-sessions cannot be combined with -p/--prompt",
                file=sys.stderr,
            )
            return 1
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
        if args.json:
            print(
                "zenagent: --json is only meaningful with -p/--prompt",
                file=sys.stderr,
            )
            return 1
        if sys.stdout.isatty() and not args.no_tui:
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
                allow_outside=args.allow_outside or [],
                allow_destructive=args.allow_destructive,
                allow_protected=args.allow_protected,
                mode=mode,
                retries=args.retries,
                max_turns=args.max_turns,
                max_cost=args.max_cost,
                thinking=args.thinking,
                trace=args.trace,
                metrics=args.metrics,
                auto_compact=not args.no_auto_compact,
            )

            broker = build_broker_for(
                user_config=load_user_config(),
                mode=mode,
                read_only=args.read_only,
                allow_dangerous=args.allow_dangerous,
                allow_outside=args.allow_outside or [],
                allow_destructive=args.allow_destructive,
                allow_protected=args.allow_protected,
            )

            async def run_tui_session() -> int:
                # Imported here so a broken install is reported by the except
                # below instead of escaping as a traceback.
                ZenAgentApp = _tui_app_class()
                runtime = await Runtime.bootstrap(
                    options, gate=broker, allow_missing_key=True
                )
                try:
                    image_blocks = _read_images(runtime.agent.model, args.images)
                    if image_blocks is None:
                        return 1
                    app = ZenAgentApp(
                        runtime,
                        theme=args.theme,
                        language=args.lang,
                        images=image_blocks,
                    )
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
                    allow_outside=args.allow_outside or [],
                    allow_destructive=args.allow_destructive,
                    allow_protected=args.allow_protected,
                    mode=mode,
                    theme=args.theme,
                    lang=args.lang,
                    templates=not args.no_templates,
                    thinking=args.thinking,
                    metrics=args.metrics,
                    images=args.images,
                    max_turns=args.max_turns,
                    max_cost=args.max_cost,
                    retries=args.retries,
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
                allow_outside=args.allow_outside or [],
                allow_destructive=args.allow_destructive,
                allow_protected=args.allow_protected,
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
