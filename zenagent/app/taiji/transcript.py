"""A read-only tail of a worker session transcript, for the board's peek.

The peek answers "what is this worker actually saying" without a full
transcript load: the last few user/assistant text turns, old to new. A torn
tail line is skipped (the session writer may be mid-flush); metadata lines
(``type`` without ``role``) were never messages.
"""

from __future__ import annotations

import json
from pathlib import Path

#: Only the tail is read: a peek must stay cheap on multi-megabyte sessions.
TAIL_WINDOW_BYTES = 2 * 1024 * 1024
TEXT_ROLES = frozenset({"user", "assistant"})


def peek_tail(session_path: str, *, limit: int = 12) -> list[dict]:
    """The last ``limit`` user/assistant text turns, old → new."""
    path = Path(session_path)
    try:
        if not path.is_file() or path.stat().st_size == 0:
            return []
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > TAIL_WINDOW_BYTES:
                handle.seek(size - TAIL_WINDOW_BYTES)
                handle.readline()  # drop the (likely torn) first line
            tail = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return []

    items: list[dict] = []
    for line in tail.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict):
            continue
        role = record.get("role")
        if role not in TEXT_ROLES:
            continue
        text = _text_of(record.get("content"))
        if text:
            items.append({"role": str(role), "text": text})
    return items[-limit:]


def _text_of(content: object) -> str:
    """The concatenated text blocks of a message content, '' when none."""
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts = [
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    return " ".join(part for part in parts if part).strip()
