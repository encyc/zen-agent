"""The ``zen rpc`` command line: argparse (shared flags) and the stdio pump."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path


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
    from zenagent.cli.rpc.host import RpcHost
    from zenagent.cli.rpc.slot import RuntimeSlot

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
    for warning in prepared.runtime.warnings:
        print(f"zenagent: {warning}", file=sys.stderr)

    initial = RuntimeSlot.adopt(prepared, write_line=_stdout_line)
    host = RpcHost(
        initial=initial,
        options=options,
        read_line=_stdin_line,
        write_line=_stdout_line,
        assume_yes=args.yes,
        warn=lambda text: print(f"zenagent: {text}", file=sys.stderr),
    )
    try:
        return await host.run()
    finally:
        await host.close()


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

