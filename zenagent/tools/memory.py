"""The ``memory`` and ``memory_write`` tools: durable project notes.

The split follows the permission gate's tool-level ``approval`` declaration
(spec: docs/superpowers/specs/2026-11-02-project-memory-design.md §4):
reads (``memory``) are ``approval="read"`` so plan mode and read-only
sessions keep them; writes (``memory_write``) are ``approval="write"``.
Both wrap the same :class:`~zenagent.agent.memory_store.MemoryStore`; entry
ids are validated 4-hex so a crafted id cannot escape ``entries/``.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from zenagent.agent.loop import AgentTool, ToolResultAgent
from zenagent.agent.memory_store import MemoryStore
from zenagent.tools._result import clip, text_result

__all__ = ["build_memory_tool", "build_memory_write_tool"]

_MAX_OUTPUT_CHARS = 20_000
_ID_ACTIONS = ("read", "update", "delete")
_READ_ACTIONS = ("read", "search")
_WRITE_ACTIONS = ("add", "update", "delete")

_READ_USAGE_POLICY = (
    "`memory` reads the project's durable notes. action=read (id) returns one "
    "entry's full text; action=search (query) lists matching entries with an "
    "excerpt. The system prompt already carries the entry index (id + title); "
    "read is for the full text, search for keyword lookups the index cannot "
    "answer."
)

_WRITE_USAGE_POLICY = (
    "`memory_write` changes the project's durable notes: action=add (title, "
    "body), update (id, title/body, reason) or delete (id). Usage policy: "
    "search before adding (avoid duplicates); prefer fewer, durable notes "
    "(decisions and their WHY, durable conventions, recurring pitfalls) over "
    "chatty state; always fill reason so the journal explains the change. "
    "Every change is appended to .zenagent/memory/journal.jsonl."
)


def _store(root: Path | None) -> MemoryStore | None:
    if root is None:
        return None
    return MemoryStore(Path(root))


def _build_store_getter(store: MemoryStore) -> Callable[[], MemoryStore]:
    def get() -> MemoryStore:
        return store

    return get


async def _read_tool(
    store: MemoryStore, tool_call_id: str, args: dict, abort
) -> ToolResultAgent:
    action = str(args.get("action") or "").strip()
    if action not in _READ_ACTIONS:
        return text_result(
            f"unknown action {action!r}; expected one of {', '.join(_READ_ACTIONS)}",
            is_error=True,
        )
    if action == "search":
        query = str(args.get("query") or "").strip()
        if not query:
            return text_result("query is required for search", is_error=True)
        matches = store.search(query)
        if not matches:
            return text_result(
                f"no entries match {query!r}", action=action, count=0
            )
        lines = [
            f"{entry.id}  {entry.title}\n    {excerpt}"
            for entry, excerpt in matches
        ]
        return text_result(
            clip("\n".join(lines), _MAX_OUTPUT_CHARS)[0], action=action, count=len(matches)
        )
    # action == "read"
    entry_id = str(args.get("id") or "").strip()
    if not _VALID_ID(entry_id):
        return text_result(f"invalid entry id {entry_id!r} (expected 4 hex chars)", is_error=True)
    entry = store.read_entry(entry_id)
    if entry is None:
        # An absent entry is an empty result, not a failure (empty library).
        return text_result(f"entry {entry_id} not found (empty library?)", action=action, count=0)
    header = f"{entry.id}  {entry.title}\n(updated {entry.updated}, source {entry.source or '-'})"
    return text_result(
        clip(f"{header}\n\n{entry.body}", _MAX_OUTPUT_CHARS)[0], action=action, count=1
    )


async def _write_tool(
    store: MemoryStore, tool_call_id: str, args: dict, abort
) -> ToolResultAgent:
    action = str(args.get("action") or "").strip()
    if action not in _WRITE_ACTIONS:
        return text_result(
            f"unknown action {action!r}; expected one of {', '.join(_WRITE_ACTIONS)}",
            is_error=True,
        )
    session = args.get("session")
    session_id = str(session) if session else None
    if action == "add":
        title = str(args.get("title") or "").strip()
        body = str(args.get("body") or "").strip()
        if not title or not body:
            return text_result("add needs title and body", is_error=True)
        entry = store.write_entry(
            title=title,
            body=body,
            source="agent",
            reason=str(args.get("reason") or ""),
            session=session_id,
        )
        return text_result(
            f"added {entry.id}  {entry.title}",
            action=action,
            id=entry.id,
        )
    entry_id = str(args.get("id") or "").strip()
    if not _VALID_ID(entry_id):
        return text_result(f"invalid entry id {entry_id!r} (expected 4 hex chars)", is_error=True)
    if action == "update":
        title = args.get("title")
        body = args.get("body")
        if title is None and body is None:
            return text_result("update needs title and/or body", is_error=True)
        entry = store.update_entry(
            entry_id,
            title=str(title) if title is not None else None,
            body=str(body) if body is not None else None,
            reason=str(args.get("reason") or ""),
            session=session_id,
        )
        if entry is None:
            return text_result(f"entry {entry_id} not found", is_error=True)
        return text_result(
            f"updated {entry.id}  {entry.title}", action=action, id=entry.id
        )
    # action == "delete"
    if not store.delete_entry(
        entry_id,
        reason=str(args.get("reason") or ""),
        session=session_id,
    ):
        return text_result(f"entry {entry_id} not found", is_error=True)
    return text_result(f"deleted {entry_id}", action=action, id=entry_id)


def _VALID_ID(entry_id: str) -> bool:
    import re

    return re.fullmatch(r"[0-9a-f]{4}", entry_id) is not None


def build_memory_tool(store: MemoryStore) -> AgentTool:
    """Read tier: action=read (one entry) / search (keyword)."""
    return AgentTool(
        name="memory",
        description=_READ_USAGE_POLICY,
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": list(_READ_ACTIONS),
                    "description": "read (id) or search (query).",
                },
                "id": {"type": "string", "description": "Entry id (read)."},
                "query": {"type": "string", "description": "Keyword (search)."},
            },
            "required": ["action"],
        },
        execute=lambda tool_call_id, args, abort, on_update=None: _read_tool(
            store, tool_call_id, args, abort
        ),
        approval="read",
        timeout_s=30.0,
    )


def build_memory_write_tool(store: MemoryStore) -> AgentTool:
    """Write tier: action=add / update / delete; every change hits the journal."""
    return AgentTool(
        name="memory_write",
        description=_WRITE_USAGE_POLICY,
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": list(_WRITE_ACTIONS),
                    "description": "add, update or delete.",
                },
                "id": {"type": "string", "description": "Entry id (update/delete)."},
                "title": {"type": "string", "description": "Entry title."},
                "body": {"type": "string", "description": "Entry body (markdown)."},
                "reason": {
                    "type": "string",
                    "description": "Why this change (goes to the journal).",
                },
            },
            "required": ["action"],
        },
        execute=lambda tool_call_id, args, abort, on_update=None: _write_tool(
            store, tool_call_id, args, abort
        ),
        approval="write",
        timeout_s=30.0,
    )
