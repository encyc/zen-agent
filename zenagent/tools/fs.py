"""Read/write/edit/list tools: the first consumer of the tool contract.

Every tool is an :class:`~zenagent.agent.loop.AgentTool` with a hand-written
JSON Schema dict (the loop's ``validate_arguments`` checks ``required`` and
top-level types against it, so the schemas must be truthful).

Paths are resolved the way the process does: relative paths are relative to the
current working directory, absolute paths are allowed anywhere (v1 has no
sandbox — the harness permission gate is what guards writes).

Failure policy follows the kernel contract: a tool that cannot do its job
returns ``is_error=True`` with an explanatory message instead of raising.
"""

from __future__ import annotations

import difflib
from pathlib import Path

from zenagent.agent.loop import AgentTool, ToolResultAgent
from zenagent.types import TextContent

__all__ = [
    "DEFAULT_READ_LIMIT",
    "EDIT_FILE",
    "LIST_DIR",
    "MAX_DIFF_LINES",
    "MAX_READ_LIMIT",
    "MAX_READ_LINES",
    "READ_FILE",
    "READ_TRUNCATION_MARKER",
    "WRITE_FILE",
]

#: Lines returned by ``read_file`` per call when the model does not say.
DEFAULT_READ_LIMIT = 400

#: Hard cap for a single ``read_file`` call (the model may ask for less).
MAX_READ_LIMIT = 2000

#: Backwards-compatible alias: the old "lines before truncation" constant.
MAX_READ_LINES = MAX_READ_LIMIT

#: Prefix of the footer ``read_file`` appends to a truncated page. ``write_file``
#: refuses to overwrite a file with content that still carries it: that content
#: is a partial read projection, not the file.
READ_TRUNCATION_MARKER = "[truncated:"

#: Total lines reported by ``list_dir`` before truncation.
MAX_LIST_ENTRIES = 500

#: How much of a binary file to sniff for NUL bytes.
_BINARY_SNIFF_BYTES = 8192

#: Lines of unified diff kept in a tool result (enough for a review, bounded).
MAX_DIFF_LINES = 200


def unified_diff(
    path: str, old: str, new: str, *, context: int = 3, max_lines: int = MAX_DIFF_LINES
) -> str:
    """Render ``old`` -> ``new`` as a bounded unified diff (no colours)."""
    lines = list(
        difflib.unified_diff(
            old.splitlines(),
            new.splitlines(),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            n=context,
            lineterm="",
        )
    )
    if len(lines) > max_lines:
        dropped = len(lines) - max_lines
        lines = lines[:max_lines] + [f"... [{dropped} more diff lines] ..."]
    return "\n".join(lines)


def _read_text_or_empty(path: Path) -> str:
    """Existing content for diffing; unreadable/binary files count as empty."""
    try:
        raw = path.read_bytes()
    except OSError:
        return ""
    if b"\x00" in raw[:_BINARY_SNIFF_BYTES]:
        return ""
    return raw.decode("utf-8", errors="replace")


def _text(text: str, *, is_error: bool = False, **details) -> ToolResultAgent:
    return ToolResultAgent(
        content=[TextContent(text=text)], details=details, is_error=is_error
    )


def _tool(
    name: str,
    description: str,
    properties: dict,
    required: list[str],
    execute,
    approval: str = "exec",
) -> AgentTool:
    return AgentTool(
        name=name,
        description=description,
        parameters={
            "type": "object",
            "properties": properties,
            "required": required,
        },
        execute=execute,
        approval=approval,
    )


def _looks_binary(data: bytes) -> bool:
    """A NUL byte in the first chunk is the classic binary signal (git-like)."""
    return b"\x00" in data[:_BINARY_SNIFF_BYTES]


async def _read_file(
    tool_call_id: str, args: dict, abort, on_update=None
) -> ToolResultAgent:
    path = Path(args["path"])
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return _text(f"file not found: {path}", is_error=True)
    except IsADirectoryError:
        return _text(f"not a file (it is a directory): {path}", is_error=True)
    except OSError as exc:
        return _text(f"cannot read {path}: {exc}", is_error=True)

    if _looks_binary(data):
        return _text(
            f"refusing to read {path}: it looks binary "
            f"({len(data)} bytes, NUL byte in the first {_BINARY_SNIFF_BYTES})",
            is_error=True,
        )
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        return _text(f"refusing to read {path}: not valid UTF-8 ({exc})", is_error=True)

    lines = text.splitlines()
    total = len(lines)
    try:
        offset = int(args.get("offset") or 0)
        limit = int(args.get("limit") or DEFAULT_READ_LIMIT)
    except (TypeError, ValueError):
        return _text("offset and limit must be integers", is_error=True)
    if offset < 0:
        return _text(
            f"offset must be >= 0 (got {offset}); it is a 0-based line index",
            is_error=True,
        )
    if limit <= 0:
        return _text(f"limit must be >= 1 (got {limit})", is_error=True)
    limit = min(limit, MAX_READ_LIMIT)

    if total == 0:
        return _text(
            "", total_lines=0, offset=offset, limit=limit, truncated=False
        )
    if offset >= total:
        return _text(
            f"offset {offset} is past EOF: {path} has {total} line(s) "
            f"(valid offsets: 0-{total - 1})",
            is_error=True,
            total_lines=total,
            offset=offset,
        )

    end = offset + min(limit, total - offset)
    details = {
        "path": str(path),
        "total_lines": total,
        "offset": offset,
        "limit": limit,
        "truncated": end < total,
    }
    # Whole-file read keeps the legacy byte-exact result (including the final
    # newline); paged reads are line slices.
    if offset == 0 and end == total:
        return _text(text, **details)

    body = "\n".join(lines[offset:end])
    if end < total:
        body += (
            f"\n\n{READ_TRUNCATION_MARKER} showing lines {offset + 1}-{end} of "
            f"{total} lines; call read_file with offset={end} for the next page]"
        )
    return _text(body, **details)


def _looks_like_truncated_read(content: str) -> bool:
    """Whether ``content`` still ends with a page footer from ``read_file``."""
    # The footer is the last thing appended to a truncated page; look at the tail
    # only so a file that merely mentions the marker in its body is not refused.
    return READ_TRUNCATION_MARKER in content[-600:]


async def _write_file(
    tool_call_id: str, args: dict, abort, on_update=None
) -> ToolResultAgent:
    path = Path(args["path"])
    content = args["content"]
    previous = _read_text_or_empty(path)
    # A model that read one page and writes it back would silently truncate the
    # file. Refuse when the payload is still a truncated read projection and is
    # smaller than what is on disk; the error tells it how to recover.
    new_bytes = len(content.encode("utf-8"))
    previous_bytes = len(previous.encode("utf-8"))
    if (
        previous_bytes
        and new_bytes < previous_bytes
        and _looks_like_truncated_read(content)
    ):
        return _text(
            f"refusing to write {path}: the content still ends with a truncated "
            "read_file projection and is shorter than the file on disk "
            f"({new_bytes} < {previous_bytes} bytes). A page is not the whole "
            "file. Re-read every page with offset/limit and write the complete "
            "content, or use edit_file for a targeted change.",
            is_error=True,
            path=str(path),
        )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except OSError as exc:
        return _text(f"cannot write {path}: {exc}", is_error=True)
    diff = unified_diff(str(path), previous, content)
    return _text(
        f"wrote {len(content)} characters to {path}", path=str(path), diff=diff
    )


async def _edit_file(
    tool_call_id: str, args: dict, abort, on_update=None
) -> ToolResultAgent:
    path = Path(args["path"])
    old = args["old_string"]
    new = args["new_string"]
    if not old:
        return _text("old_string must not be empty", is_error=True)

    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _text(f"file not found: {path}", is_error=True)
    except (OSError, UnicodeDecodeError) as exc:
        return _text(f"cannot read {path}: {exc}", is_error=True)

    matches = text.count(old)
    if matches == 0:
        return _text(
            f"old_string not found in {path}; nothing changed. Include more "
            "surrounding context.",
            is_error=True,
            matches=0,
        )
    if matches > 1:
        return _text(
            f"old_string matches {matches} times in {path}; it must be unique. "
            "Include more surrounding context; nothing changed.",
            is_error=True,
            matches=matches,
        )

    updated = text.replace(old, new, 1)
    try:
        path.write_text(updated, encoding="utf-8")
    except OSError as exc:
        return _text(f"cannot write {path}: {exc}", is_error=True)
    diff = unified_diff(str(path), text, updated)
    return _text(
        f"edited {path}: replaced 1 occurrence", path=str(path), diff=diff
    )


def _format_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


async def _list_dir(
    tool_call_id: str, args: dict, abort, on_update=None
) -> ToolResultAgent:
    path = Path(args["path"])
    try:
        entries = sorted(path.iterdir(), key=lambda entry: (not entry.is_dir(), entry.name))
    except FileNotFoundError:
        return _text(f"directory not found: {path}", is_error=True)
    except NotADirectoryError:
        return _text(f"not a directory: {path}", is_error=True)
    except OSError as exc:
        return _text(f"cannot list {path}: {exc}", is_error=True)

    rows: list[str] = [f"{path.resolve()} ({len(entries)} entries)"]
    shown = entries[:MAX_LIST_ENTRIES]
    for entry in shown:
        try:
            if entry.is_dir():
                rows.append(f"d  {entry.name}/")
            else:
                rows.append(f"f  {entry.name}  ({_format_size(entry.stat().st_size)})")
        except OSError:
            rows.append(f"?  {entry.name}  (unreadable)")
    if len(entries) > len(shown):
        rows.append(f"... {len(entries) - len(shown)} more entries not shown")
    return _text("\n".join(rows), entries=len(entries))


READ_FILE = _tool(
    "read_file",
    "Read a UTF-8 text file. Binary files are refused. Returns whole small "
    f"files; at most {DEFAULT_READ_LIMIT} lines per call by default (hard cap "
    f"{MAX_READ_LIMIT}). A truncated result reports the exact offset for the "
    "next page.",
    {
        "path": {"type": "string", "description": "File path to read."},
        "offset": {
            "type": "integer",
            "description": "0-based line index to start from (default 0).",
        },
        "limit": {
            "type": "integer",
            "description": (
                f"Maximum lines to return (default {DEFAULT_READ_LIMIT}, "
                f"cap {MAX_READ_LIMIT})."
            ),
        },
    },
    ["path"],
    _read_file,
    approval="read",
)

WRITE_FILE = _tool(
    "write_file",
    "Write UTF-8 text to a file, creating parent directories as needed. "
    "Overwrites the whole file.",
    {
        "path": {"type": "string", "description": "File path to write."},
        "content": {"type": "string", "description": "Full file content."},
    },
    ["path", "content"],
    _write_file,
    approval="write",
)

EDIT_FILE = _tool(
    "edit_file",
    "Replace one exact string in a file. old_string must match exactly once; "
    "on zero or multiple matches the file is left unchanged.",
    {
        "path": {"type": "string", "description": "File to edit."},
        "old_string": {
            "type": "string",
            "description": "Exact text to replace (must be unique in the file).",
        },
        "new_string": {"type": "string", "description": "Replacement text."},
    },
    ["path", "old_string", "new_string"],
    _edit_file,
    approval="write",
)

LIST_DIR = _tool(
    "list_dir",
    "List one directory level, marking directories and file sizes.",
    {"path": {"type": "string", "description": "Directory to list."}},
    ["path"],
    _list_dir,
    approval="read",
)
