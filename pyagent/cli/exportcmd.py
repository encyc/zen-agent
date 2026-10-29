"""``pyagent export``: turn a saved session into a shareable document."""

from __future__ import annotations

import sys
from pathlib import Path

from pyagent.agent.session import latest_session
from pyagent.export import export_session

__all__ = ["run_export"]


def run_export(
    target: str | None = None,
    fmt: str = "md",
    out: Path | None = None,
    print_fn=print,
) -> int:
    """Export a session file (or the latest one) and print the written path."""
    if target in (None, "", "latest"):
        path = latest_session()
        if path is None:
            print("pyagent: no saved sessions", file=sys.stderr)
            return 1
    else:
        path = Path(target).expanduser()
        if not path.is_file():
            print(f"pyagent: no such session: {path}", file=sys.stderr)
            return 1
    try:
        written = export_session(path, fmt, out)
    except ValueError as exc:
        print(f"pyagent: {exc}", file=sys.stderr)
        return 1
    print_fn(str(written))
    return 0
