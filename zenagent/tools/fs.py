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
    "EDIT_FILE",
    "LIST_DIR",
    "MAX_DIFF_LINES",
    "READ_FILE",
    "WRITE_FILE",
]

#: Lines returned by ``read_file`` before truncation kicks in.
MAX_READ_LINES = 2000

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
    if len(lines) > MAX_READ_LINES:
        shown = lines[:MAX_READ_LINES]
        body = "\n".join(shown)
        suffix = (
            f"\n\n[truncated: showing {MAX_READ_LINES} of {len(lines)} lines; "
            "read a smaller range or use grep]"
        )
        return _text(body + suffix, truncated=True, total_lines=len(lines))
    return _text(text, total_lines=len(lines))


async def _write_file(
    tool_call_id: str, args: dict, abort, on_update=None
) -> ToolResultAgent:
    path = Path(args["path"])
    content = args["content"]
    previous = _read_text_or_empty(path)
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
    "Read a UTF-8 text file. Binary files are refused. Output is truncated "
    f"after {MAX_READ_LINES} lines.",
    {"path": {"type": "string", "description": "File path to read."}},
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
