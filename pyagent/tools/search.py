"""Read-only search tools: ``glob`` and ``grep``.

``glob`` is pure pathlib. ``grep`` prefers ripgrep (fast, respects .gitignore)
and falls back to a pure-Python line scan when ``rg`` is not installed, so the
agent still works on a bare machine.

Both return at most a fixed number of rows and say so when they truncate.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
from pathlib import Path

from pyagent.agent.loop import AgentTool, ToolResultAgent
from pyagent.types import TextContent

__all__ = ["BATCH", "GLOB", "GREP", "MAX_GLOB_RESULTS", "MAX_GREP_RESULTS"]

#: Cap on paths returned by ``glob``.
MAX_GLOB_RESULTS = 500
#: Cap on matches returned by ``grep``.
MAX_GREP_RESULTS = 200
#: A NUL byte in this prefix marks a binary file for the fallback scanner.
_BINARY_SNIFF = 8192


def _text(text: str, *, is_error: bool = False, **details) -> ToolResultAgent:
    return ToolResultAgent(
        content=[TextContent(text=text)], details=details, is_error=is_error
    )


async def _glob(tool_call_id: str, args: dict, abort, on_update=None) -> ToolResultAgent:
    pattern = args["pattern"]
    root = Path(args.get("path") or ".")
    if not root.is_dir():
        return _text(f"not a directory: {root}", is_error=True)
    try:
        matches = sorted(
            str(path.relative_to(root) if path != root else path)
            for path in root.rglob(pattern)
            if path.is_file()
        )
    except (ValueError, NotImplementedError, OSError) as exc:
        return _text(f"invalid glob pattern {pattern!r}: {exc}", is_error=True)

    total = len(matches)
    shown = matches[:MAX_GLOB_RESULTS]
    lines = list(shown)
    if total > len(shown):
        lines.append(f"... {total - len(shown)} more matches not shown")
    body = "\n".join(lines) if lines else f"no files match {pattern!r} under {root}"
    return _text(body, matches=total)


def _python_grep(pattern: re.Pattern, root: Path, file_glob: str) -> list[str]:
    """Fallback line scan used when ripgrep is unavailable."""
    results: list[str] = []
    for path in sorted(root.rglob(file_glob)):
        if not path.is_file():
            continue
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        if b"\x00" in raw[:_BINARY_SNIFF]:
            continue
        text = raw.decode("utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                results.append(f"{path}:{lineno}:{line.strip()}")
                if len(results) >= MAX_GREP_RESULTS:
                    return results
    return results


async def _rg_grep(
    pattern: str, root: Path, file_glob: str
) -> list[str] | None:
    """Run ``rg --json``; returns None when rg is missing or fails to start."""
    rg = shutil.which("rg")
    if rg is None:
        return None
    args = [
        rg,
        "--json",
        "--line-number",
        "--no-heading",
        "--color",
        "never",
        "-e",
        pattern,
        "--glob",
        file_glob,
        str(root),
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError:
        return None
    assert proc.stdout is not None
    results: list[str] = []
    async for raw in proc.stdout:
        try:
            event = json.loads(raw)
        except ValueError:
            continue
        if event.get("type") != "match":
            continue
        data = event.get("data") or {}
        path = (data.get("path") or {}).get("text")
        line_number = data.get("line_number")
        line = ((data.get("lines") or {}).get("text") or "").rstrip("\n")
        if path is None or line_number is None:
            continue
        results.append(f"{path}:{line_number}:{line.strip()}")
        if len(results) >= MAX_GREP_RESULTS:
            break
    if proc.returncode is None:
        with __import__("contextlib").suppress(ProcessLookupError):
            proc.kill()
    await proc.wait()
    return results


async def _grep(tool_call_id: str, args: dict, abort, on_update=None) -> ToolResultAgent:
    pattern_text = args["pattern"]
    root = Path(args.get("path") or ".")
    file_glob = args.get("glob") or "*"
    if not root.exists():
        return _text(f"path not found: {root}", is_error=True)
    try:
        compiled = re.compile(pattern_text)
    except re.error as exc:
        return _text(f"invalid regular expression {pattern_text!r}: {exc}", is_error=True)

    results = await _rg_grep(pattern_text, root, file_glob)
    engine = "rg"
    if results is None:
        results = _python_grep(compiled, root, file_glob)
        engine = "python"

    if not results:
        return _text(
            f"no matches for {pattern_text!r} under {root}", matches=0, engine=engine
        )
    lines = list(results)
    if len(results) >= MAX_GREP_RESULTS:
        lines.append(f"... stopped at {MAX_GREP_RESULTS} matches")
    return _text("\n".join(lines), matches=len(results), engine=engine)


GLOB = AgentTool(
    name="glob",
    description=(
        "Find files by glob pattern (e.g. '**/*.py') under a directory. "
        f"Returns at most {MAX_GLOB_RESULTS} paths."
    ),
    parameters={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Glob pattern, e.g. **/*.py"},
            "path": {"type": "string", "description": "Directory to search (default .)."},
        },
        "required": ["pattern"],
    },
    execute=_glob,
)

GREP = AgentTool(
    name="grep",
    description=(
        "Search file contents with a regular expression. Uses ripgrep when "
        f"available, otherwise a slow Python scan. At most {MAX_GREP_RESULTS} matches."
    ),
    parameters={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Python/RE2 regular expression."},
            "path": {"type": "string", "description": "Directory to search (default .)."},
            "glob": {
                "type": "string",
                "description": "Only search files matching this glob (default *).",
            },
        },
        "required": ["pattern"],
    },
    execute=_grep,
)

#: Convenience alias used by tests/importers that want the pair.
BATCH = [GLOB, GREP]
