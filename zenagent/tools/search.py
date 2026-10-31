"""Read-only search tools: ``glob`` and ``grep``.

``glob`` is pure pathlib. ``grep`` prefers ripgrep (fast, respects .gitignore)
and falls back to a pure-Python line scan when ``rg`` is not installed, so the
agent still works on a bare machine.

Both return a bounded result and say so when they truncate. ``grep`` pages by
file (not by hit): a page holds at most ``MAX_GREP_FILES_PER_PAGE`` files with
at most ``MAX_GREP_HITS_PER_FILE`` hits each, and ``skip`` addresses the next
page. Both engines share the same collector and renderer so they paginate
identically.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import shutil
from pathlib import Path

from zenagent.agent.loop import AgentTool, ToolResultAgent
from zenagent.types import TextContent

__all__ = [
    "BATCH",
    "GLOB",
    "GREP",
    "MAX_GLOB_RESULTS",
    "MAX_GREP_FILES_PER_PAGE",
    "MAX_GREP_HITS_PER_FILE",
    "MAX_GREP_RESULTS",
]

#: Default/maximum paths returned by ``glob``.
MAX_GLOB_RESULTS = 500
MAX_GLOB_LIMIT = 2_000
#: Internal cap on hits collected by one ``grep`` call. This bounds the scan
#: (memory and time); it is *not* the flat output cap anymore — results are
#: paged by file. A capped scan says so and asks for a narrower pattern.
MAX_GREP_RESULTS = 2000
#: Distinct files with matches shown per ``grep`` page.
MAX_GREP_FILES_PER_PAGE = 20
#: Hits shown for a single file before its remainder is elided.
MAX_GREP_HITS_PER_FILE = 20
#: A NUL byte in this prefix marks a binary file for the fallback scanner.
_BINARY_SNIFF = 8192


def _text(text: str, *, is_error: bool = False, **details) -> ToolResultAgent:
    return ToolResultAgent(
        content=[TextContent(text=text)], details=details, is_error=is_error
    )


class _GrepHits:
    """Per-file match collection shared by the rg and Python engines.

    The caps live here — not in either scan loop — so both engines produce
    exactly the same pages for the same tree.
    """

    def __init__(self) -> None:
        self.by_file: dict[str, list[tuple[int, str]]] = {}
        self.capped: set[str] = set()
        self.scan_capped = False
        self.total = 0

    def add(self, path: str, line_number: int, line: str) -> bool:
        """Record one hit; returns False when the scan can stop."""
        if self.scan_capped:
            return False
        bucket = self.by_file.get(path)
        if bucket is None:
            bucket = self.by_file[path] = []
        elif path in self.capped:
            return True  # a later file may still fit in the scan window
        if len(bucket) < MAX_GREP_HITS_PER_FILE:
            bucket.append((line_number, line))
            self.total += 1
            if self.total >= MAX_GREP_RESULTS:
                self.scan_capped = True
                return False
            return True
        self.capped.add(path)
        return True


async def _glob(tool_call_id: str, args: dict, abort, on_update=None) -> ToolResultAgent:
    pattern = args["pattern"]
    root = Path(args.get("path") or ".")
    if not root.is_dir():
        return _text(f"not a directory: {root}", is_error=True)
    try:
        limit = MAX_GLOB_RESULTS if args.get("limit") is None else int(args["limit"])
    except (TypeError, ValueError):
        return _text("limit must be an integer", is_error=True)
    if limit <= 0:
        return _text(f"limit must be >= 1 (got {limit})", is_error=True)
    limit = min(limit, MAX_GLOB_LIMIT)
    include_hidden = bool(args.get("include_hidden", False))

    try:
        found: list[str] = []
        for path in root.rglob(pattern):
            if not path.is_file():
                continue
            rel = path.relative_to(root) if path != root else path
            parts = rel.parts
            if ".git" in parts:  # never useful, potentially enormous
                continue
            if not include_hidden and any(part.startswith(".") for part in parts):
                continue
            found.append(str(rel) if path != root else str(path))
        matches = sorted(found)
    except (ValueError, NotImplementedError, OSError) as exc:
        return _text(f"invalid glob pattern {pattern!r}: {exc}", is_error=True)

    total = len(matches)
    shown = matches[:limit]
    lines = list(shown)
    if total > len(shown):
        lines.append(f"... {total - len(shown)} more matches not shown")
    body = "\n".join(lines) if lines else f"no files match {pattern!r} under {root}"
    return _text(body, matches=total, shown=len(shown), limit=limit)


def _python_grep(pattern: re.Pattern, root: Path, file_glob: str) -> _GrepHits:
    """Fallback line scan used when ripgrep is unavailable."""
    hits = _GrepHits()
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
            if pattern.search(line) and not hits.add(
                str(path), lineno, line.strip()
            ):
                return hits
    return hits


async def _rg_grep(
    pattern: str, root: Path, file_glob: str
) -> _GrepHits | None:
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
    hits = _GrepHits()
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
        # Normalize "./x" to "x" so both engines name files identically.
        if not hits.add(str(Path(path)), line_number, line.strip()):
            break
    if proc.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
    await proc.wait()
    return hits


def _grep_page(
    hits: _GrepHits, pattern_text: str, root: Path, skip: int, engine: str
) -> ToolResultAgent:
    """Render one file-addressed page of ``hits`` (shared by both engines)."""
    files = sorted(hits.by_file)
    total_files = len(files)
    if total_files == 0 and skip == 0:
        return _text(
            f"no matches for {pattern_text!r} under {root}",
            matches=0,
            files=0,
            truncated=False,
            next_skip=None,
            engine=engine,
            skip=skip,
            total_files=0,
        )
    if skip >= total_files:
        return _text(
            f"skip={skip} is past the last matching file: only {total_files} "
            f"file(s) have matches for {pattern_text!r} under {root}; nothing "
            "to show (omit skip to search from the first file)",
            matches=0,
            files=0,
            truncated=False,
            next_skip=None,
            engine=engine,
            skip=skip,
            total_files=total_files,
        )

    page = files[skip : skip + MAX_GREP_FILES_PER_PAGE]
    lines: list[str] = []
    hits_shown = 0
    page_capped = False
    for name in page:
        for line_number, line in hits.by_file[name]:
            lines.append(f"{name}:{line_number}:{line}")
            hits_shown += 1
        if name in hits.capped:
            page_capped = True
            lines.append(
                f"... [max {MAX_GREP_HITS_PER_FILE} matches per file: {name} "
                "has more matches; narrow the pattern to see the rest]"
            )
    end = skip + len(page)
    more_files = end < total_files
    if more_files:
        lines.append(
            f"[... showing files {skip + 1}-{end} of {total_files}; call grep "
            f"with skip={end} for the next page]"
        )
    if hits.scan_capped:
        lines.append(
            f"... [scan stopped at {MAX_GREP_RESULTS} matches; narrow the "
            "pattern to see more]"
        )
    return _text(
        "\n".join(lines),
        matches=hits_shown,
        files=len(page),
        truncated=more_files or page_capped or hits.scan_capped,
        next_skip=end if more_files else None,
        engine=engine,
        skip=skip,
        total_files=total_files,
    )


async def _grep(tool_call_id: str, args: dict, abort, on_update=None) -> ToolResultAgent:
    pattern_text = args["pattern"]
    root = Path(args.get("path") or ".")
    file_glob = args.get("glob") or "*"
    skip = args.get("skip", 0)
    if skip is None:
        skip = 0
    if isinstance(skip, bool) or not isinstance(skip, int):
        return _text(
            f"skip must be a non-negative integer (got {skip!r})", is_error=True
        )
    if skip < 0:
        return _text(
            f"skip must be >= 0 (got {skip}); it counts matching files, "
            "not matches",
            is_error=True,
        )
    if not root.exists():
        return _text(f"path not found: {root}", is_error=True)
    try:
        compiled = re.compile(pattern_text)
    except re.error as exc:
        return _text(f"invalid regular expression {pattern_text!r}: {exc}", is_error=True)

    hits = await _rg_grep(pattern_text, root, file_glob)
    engine = "rg"
    if hits is None:
        hits = _python_grep(compiled, root, file_glob)
        engine = "python"
    return _grep_page(hits, pattern_text, root, skip, engine)


GLOB = AgentTool(
    name="glob",
    description=(
        "Find files by glob pattern (e.g. '**/*.py') under a directory. Hidden "
        f"files are skipped unless include_hidden; returns at most limit paths "
        f"(default {MAX_GLOB_RESULTS})."
    ),
    parameters={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Glob pattern, e.g. **/*.py"},
            "path": {"type": "string", "description": "Directory to search (default .)."},
            "limit": {
                "type": "integer",
                "description": (
                    f"Maximum paths to return (default {MAX_GLOB_RESULTS}, "
                    f"cap {MAX_GLOB_LIMIT})."
                ),
            },
            "include_hidden": {
                "type": "boolean",
                "description": "Include dotfiles/dot-directories (.git is always skipped).",
            },
        },
        "required": ["pattern"],
    },
    execute=_glob,
    approval="read",
)

GREP = AgentTool(
    name="grep",
    description=(
        "Search file contents with a regular expression. Uses ripgrep when "
        "available, otherwise a slow Python scan. Results are grouped by file "
        f"and paged: at most {MAX_GREP_FILES_PER_PAGE} matching files and "
        f"{MAX_GREP_HITS_PER_FILE} hits per file per call. A truncated page "
        "reports the skip value for the next page."
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
            "skip": {
                "type": "integer",
                "default": 0,
                "description": (
                    "Number of matching files to skip for pagination (a page "
                    f"is {MAX_GREP_FILES_PER_PAGE} files; use the value the "
                    "footer reports)."
                ),
            },
        },
        "required": ["pattern"],
    },
    execute=_grep,
    approval="read",
)

#: Convenience alias used by tests/importers that want the pair.
BATCH = [GLOB, GREP]
