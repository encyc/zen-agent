"""The ``zen rpc`` command line: argparse (shared flags) and the stdio pump."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from zenagent.app.taiji import (
    close_taiji,
    ensure_taiji_session,
    own_taiji_lock,
    release_taiji_lock,
    should_init_taiji,
)
from zenagent.cli.rpc.server import RpcServer
from zenagent.project import (
    project_dir,
    project_sessions_dir,
)


async def _stdin_line() -> str | None:
    line = await asyncio.to_thread(sys.stdin.readline)
    return None if line == "" else line


def _stdout_line(text: str) -> None:
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


async def _run(args) -> int:
    from zenagent.app.bootstrap import prepare
    from zenagent.app.modes import resolve_mode
    from zenagent.app.runtime import RuntimeOptions

    # Resolve the mode exactly like the other frontends: an explicit --mode
    # wins, then --read-only (plan), then --yes (zen). Passing the raw
    # ``args.mode`` (None when the flag is omitted) into the gate used to
    # raise "unknown mode: None" for every documented ``zen rpc`` invocation.
    mode = resolve_mode(
        explicit=args.mode, assume_yes=args.yes, read_only=args.read_only
    )
    options = RuntimeOptions(
        root=Path.cwd(),
        model_id=args.model,
        thinking=args.thinking,
        resume=args.resume,
        mcp=not args.no_mcp,
        mcp_file=Path(args.mcp_file) if args.mcp_file else None,
        mcp_servers=args.mcp_servers,
        skills=not args.no_skills,
        templates=not args.no_templates,
        extension_paths=args.extension_paths or [],
        context_files=not args.no_context_files,
        auto_compact=not args.no_auto_compact,
        read_only=args.read_only,
        allow_dangerous=args.allow_dangerous,
        allow_outside=args.allow_outside or [],
        allow_destructive=args.allow_destructive,
        allow_protected=args.allow_protected,
        mode=mode,
        retries=args.retries,
        max_turns=args.max_turns,
        max_cost=args.max_cost,
        trace=args.trace,
        metrics=args.metrics,
    )
    try:
        prepared = await prepare(options, assume_yes=args.yes)
    except KeyError as exc:
        print(f"zenagent: unknown model: {exc}", file=sys.stderr)
        return 2
    except (RuntimeError, LookupError) as exc:
        print(f"zenagent: {exc}", file=sys.stderr)
        return 2
    runtime = prepared.runtime
    chat = prepared.chat
    services = prepared.services
    for warning in runtime.warnings:
        print(f"zenagent: {warning}", file=sys.stderr)

    server = RpcServer(
        runtime=runtime,
        read_line=_stdin_line,
        write_line=_stdout_line,
        chat=chat,
        models=services.models,
        sessions=services.sessions,
        commands=prepared.commands,
    )
    prepared.broker.set_confirm(server.confirm)
    runtime.ask_handler = server.ask
    runtime.taiji_chat = chat
    taiji_project_dir = project_dir(Path.cwd())
    taiji_hosts = should_init_taiji(Path.cwd())
    if taiji_hosts:
        ensure_taiji_session(project_sessions_dir(Path.cwd()))
    taiji_owned = taiji_hosts and own_taiji_lock(taiji_project_dir)
    if not taiji_owned:
        runtime._note("taiji: 另一个宿主已持有本项目的太极锁")
    elif taiji_hosts:
        # The lock holder constructs the fleet at boot: the registry
        # reconciles immediately, so the panel sees the truth from its
        # very first board poll (spec 附录 C.3).
        from zenagent.app.taiji.tools import taiji_supervisor_for

        taiji_supervisor_for(runtime)
    try:
        return await server.run()
    finally:
        runtime.ask_handler = None
        await close_taiji()
        if taiji_owned:
            release_taiji_lock(taiji_project_dir)
        await runtime.close()


def main(argv: list[str] | None = None) -> int:
    from zenagent.cli.flags import add_runtime_flags

    parser = argparse.ArgumentParser(
        prog="zenagent rpc", description=__doc__.splitlines()[0]
    )
    add_runtime_flags(parser)
    args = parser.parse_args(argv)
    if args.cwd:
        import os

        target = Path(args.cwd).expanduser()
        if not target.is_dir():
            print(f"zenagent: --cwd is not a directory: {target}", file=sys.stderr)
            return 1
        os.chdir(target.resolve())
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        return 130
    except ValueError as exc:
        # A bad programmatic value (e.g. an invalid mode supplied by an
        # embedder rather than through argparse choices) must be a clean
        # stderr line + exit 2, not a traceback.
        print(f"zenagent: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

