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
import re
from pathlib import Path

from zenagent.agent.loop import AgentTool, ToolResultAgent
from zenagent.agent.result_budget import LINE_TRUNCATION_MARKER
from zenagent.prompts import tool_description
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

#: UTF-8 BOM: as a character (what decoding yields) and as bytes on disk.
_BOM = "\ufeff"
_BOM_BYTES = b"\xef\xbb\xbf"


def _read_existing(path: Path) -> tuple[str, bytes | None]:
    """Return ``(decoded_text, raw_bytes)`` for an existing file.

    The text is what diffing compares against; unreadable and binary files have
    empty text. ``raw`` is the on-disk bytes (when readable) so callers can
    detect the file's BOM/newline convention without a second read.
    """
    try:
        raw = path.read_bytes()
    except OSError:
        return "", None
    if b"\x00" in raw[:_BINARY_SNIFF_BYTES]:
        return "", raw
    return raw.decode("utf-8", errors="replace"), raw


def _read_text_or_empty(path: Path) -> str:
    """Existing content for diffing; unreadable/binary files count as empty."""
    return _read_existing(path)[0]


def _detect_file_conventions(raw: bytes) -> tuple[bool, str]:
    """Return ``(has_bom, newline)`` for the bytes of an existing file."""
    has_bom = raw.startswith(_BOM_BYTES)
    return has_bom, "\r\n" if b"\r\n" in raw else "\n"


def _apply_file_conventions(text: str, *, has_bom: bool, newline: str) -> str:
    """Re-apply an existing file's BOM and newline style to ``text``.

    LF-only model text is written back as CRLF for a CRLF file, while text that
    already uses CRLF is not double-converted. A file without a BOM keeps one
    the model explicitly included; a file with a BOM gets exactly one.
    """
    if has_bom and text.startswith(_BOM):
        text = text[1:]  # the BOM is re-added below, so this would be a dupe
    if newline == "\r\n":
        text = text.replace("\r\n", "\n").replace("\n", "\r\n")
    return _BOM + text if has_bom else text


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
        offset = 0 if args.get("offset") is None else int(args["offset"])
        limit = (
            DEFAULT_READ_LIMIT if args.get("limit") is None else int(args["limit"])
        )
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

    if total == 0 and offset == 0:
        return _text(
            "", total_lines=0, offset=offset, limit=limit, truncated=False
        )
    if offset >= total:
        return _text(
            f"offset {offset} is past EOF: {path} has {total} line(s) "
            f"(valid offsets: 0-{max(total - 1, 0)})",
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


#: Footer ``read_file`` appends to a truncated page (anchored at end of text).
_PAGE_FOOTER_RE = re.compile(
    r"\n\n\[truncated: showing lines \d+-\d+ of \d+ lines; "
    r"call read_file with offset=\d+ for the next page\]\s*$"
)

#: Kernel result-budget notice, which can sit between the kept head and tail.
_BUDGET_NOTICE_RE = re.compile(
    r"\n\n… \[truncated \d+ of \d+ bytes: .*?\] …\n\n", re.DOTALL
)


def _strip_read_projection(content: str) -> tuple[str, bool]:
    """Return ``(body, was_projected)`` for a possibly truncated read result.

    A read projection is any result the model received that is *not* the whole
    file: a paged read (page footer), a kernel-budget truncation (byte notice),
    or a line-capped result (line marker). Stripping the notices lets the write
    guard compare the real body with what is on disk; testing the un-stripped
    length is what let a 401-line page (longer on disk because of its footer)
    slip through.
    """
    body = content
    projected = False
    match = _PAGE_FOOTER_RE.search(body)
    if match is not None:
        projected = True
        body = body[: match.start()]
    match = _BUDGET_NOTICE_RE.search(body)
    if match is not None:
        projected = True
        body = body[: match.start()] + body[match.end() :]
    if LINE_TRUNCATION_MARKER in content:
        projected = True
    return body, projected


async def _write_file(
    tool_call_id: str, args: dict, abort, on_update=None
) -> ToolResultAgent:
    path = Path(args["path"])
    content = args["content"]
    previous, raw = _read_existing(path)
    # A model that read one page and writes it back would silently truncate the
    # file. Refuse when the payload is still a truncated read projection and is
    # smaller than what is on disk; the error tells it how to recover.
    body, projected = _strip_read_projection(content)
    body_bytes = len(body.encode("utf-8"))
    previous_bytes = len(previous.encode("utf-8"))
    if previous_bytes and projected and body_bytes < previous_bytes:
        return _text(
            f"refusing to write {path}: the content is a truncated read_file "
            "projection (page footer, budget or line truncation) and its body "
            f"is shorter than the file on disk ({body_bytes} < "
            f"{previous_bytes} bytes). A page is not the whole file. Re-read "
            "every page with offset/limit and write the complete content, or "
            "use edit_file for a targeted change.",
            is_error=True,
            path=str(path),
        )
    # Preserve an existing text file's conventions; new files stay LF/no-BOM.
    has_bom, newline = False, "\n"
    if raw is not None and previous:
        has_bom, newline = _detect_file_conventions(raw)
    if content == "":
        # An empty write means an empty file: do not leave a BOM/newline corpse.
        has_bom, newline = False, "\n"
    payload = _apply_file_conventions(content, has_bom=has_bom, newline=newline)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload.encode("utf-8"))
    except OSError as exc:
        return _text(f"cannot write {path}: {exc}", is_error=True)
    diff = unified_diff(str(path), previous, payload)
    notice = ""
    if payload != content:
        notice = " (kept the existing file's CRLF/BOM convention)"
    return _text(
        f"wrote {len(content)} characters to {path}{notice}",
        path=str(path),
        diff=diff,
    )


async def _edit_file(
    tool_call_id: str, args: dict, abort, on_update=None
) -> ToolResultAgent:
    path = Path(args["path"])
    old = args["old_string"]
    new = args["new_string"]
    replace_all = args.get("replace_all", False)
    if replace_all is None:
        replace_all = False
    if not isinstance(replace_all, bool):
        return _text("replace_all must be a boolean", is_error=True)
    if not old:
        return _text("old_string must not be empty", is_error=True)

    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return _text(f"file not found: {path}", is_error=True)
    except OSError as exc:
        return _text(f"cannot read {path}: {exc}", is_error=True)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return _text(f"cannot read {path}: {exc}", is_error=True)

    # Match newline-insensitively: a model editing a CRLF file often supplies
    # LF-only old_string/new_string, and an LF file may receive CRLF text.
    # The result is converted back to the file's own convention on write.
    has_bom, newline = _detect_file_conventions(raw)
    if has_bom and text.startswith(_BOM):
        # Match without the BOM and re-add it on write, so an edit whose
        # old_string mentions \ufeff cannot silently drop the BOM.
        text = text[1:]
    searchable = text.replace("\r\n", "\n")
    target = old.replace("\r\n", "\n")
    replacement = new.replace("\r\n", "\n")

    matches = searchable.count(target)
    if matches == 0:
        return _text(
            f"old_string not found in {path}; nothing changed. Include more "
            "surrounding context.",
            is_error=True,
            matches=0,
        )
    if matches > 1 and not replace_all:
        return _text(
            f"old_string matches {matches} times in {path}; it must be unique. "
            "Include more surrounding context; nothing changed.",
            is_error=True,
            matches=matches,
        )

    updated_searchable = searchable.replace(
        target, replacement, -1 if replace_all else 1
    )
    if newline == "\r\n":
        updated = updated_searchable.replace("\n", "\r\n")
    else:
        updated = updated_searchable
    if has_bom:
        updated = _BOM + updated
    try:
        path.write_bytes(updated.encode("utf-8"))
    except OSError as exc:
        return _text(f"cannot write {path}: {exc}", is_error=True)
    diff = unified_diff(str(path), text, updated)
    count = matches if replace_all else 1
    noun = "occurrence" if count == 1 else "occurrences"
    return _text(
        f"edited {path}: replaced {count} {noun}",
        path=str(path),
        diff=diff,
        replacements=count,
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
    tool_description(
        "read_file",
        default_read_limit=str(DEFAULT_READ_LIMIT),
        max_read_limit=str(MAX_READ_LIMIT),
    ),
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
    tool_description("write_file"),
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
    tool_description("edit_file"),
    {
        "path": {"type": "string", "description": "File to edit."},
        "old_string": {
            "type": "string",
            "description": (
                "Exact text to replace (must be unique unless replace_all)."
            ),
        },
        "new_string": {"type": "string", "description": "Replacement text."},
        "replace_all": {
            "type": "boolean",
            "default": False,
            "description": (
                "Replace every occurrence instead of requiring a unique match."
            ),
        },
    },
    ["path", "old_string", "new_string"],
    _edit_file,
    approval="write",
)

LIST_DIR = _tool(
    "list_dir",
    tool_description("list_dir"),
    {"path": {"type": "string", "description": "Directory to list."}},
    ["path"],
    _list_dir,
    approval="read",
)
