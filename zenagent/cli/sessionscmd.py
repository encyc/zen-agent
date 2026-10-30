"""``zen sessions``: project-scoped session listing and legacy adoption.

Kept free of Runtime/Agent construction so it works without an API key.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

__all__ = ["build_parser", "main"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="zen sessions", description="list sessions or adopt legacy ones"
    )
    subcommands = parser.add_subparsers(dest="action", required=True)

    list_parser = subcommands.add_parser(
        "list", help="list this project's sessions (or every project)"
    )
    list_parser.add_argument(
        "--all", action="store_true", help="include every project and legacy sessions"
    )
    list_parser.add_argument("--json", action="store_true", help="emit JSON")

    adopt_parser = subcommands.add_parser(
        "adopt", help="move a legacy session into the current project"
    )
    adopt_parser.add_argument("session", help="path to a legacy session JSONL")

    return parser


def _list(all_projects: bool, as_json: bool) -> int:
    from zenagent.agent.session import list_sessions

    infos = list_sessions(scope="all" if all_projects else "project")
    if as_json:
        print(
            json.dumps(
                [
                    {
                        "path": str(info.path),
                        "title": info.title,
                        "project": info.project,
                        "messages": info.messages,
                        "mtime": info.mtime,
                    }
                    for info in infos
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if not infos:
        print("no saved sessions")
        if not all_projects:
            print("hint: zen sessions list --all")
        return 0
    for position, info in enumerate(infos, start=1):
        label = f"[{info.project or 'legacy'}] " if all_projects else ""
        print(
            f"{position:2d}. {label}{info.title or '(empty)'} · "
            f"{info.messages} msgs · {info.path.name}"
        )
    return 0


def _adopt(target: str) -> int:
    from zenagent.app.sessions import adopt_legacy_session

    path = Path(target).expanduser()
    try:
        adopted = adopt_legacy_session(path)
    except ValueError as exc:
        print(f"zenagent: {exc}", file=sys.stderr)
        return 2
    print(f"adopted: {adopted}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.action == "list":
        return _list(args.all, args.json)
    if args.action == "adopt":
        return _adopt(args.session)
    return 2
