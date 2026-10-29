"""``pyagent usage``: tokens and spend across saved sessions."""

from __future__ import annotations

import json
import sys

from pyagent.usage import collect_usage, render_report, session_paths

__all__ = ["run_usage"]


def run_usage(since: str | None = None, json_output: bool = False, print_fn=print) -> int:
    try:
        paths = session_paths(since)
    except ValueError as exc:
        print(f"pyagent: bad --since value: {exc}", file=sys.stderr)
        return 1
    report = collect_usage(paths)
    if json_output:
        print_fn(json.dumps(report.to_json(), ensure_ascii=False, indent=2))
    else:
        print_fn(render_report(report))
    return 0
