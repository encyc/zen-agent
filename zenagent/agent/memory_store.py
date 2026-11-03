"""Project memory store: durable per-project notes under ``.zenagent/memory/``.

Layout (spec: docs/superpowers/specs/2026-11-02-project-memory-design.md):

    <project-root>/.zenagent/memory/
      journal.jsonl          append-only change log (one JSON event per line)
      entries/<id>.md        one entry per file: minimal frontmatter + free md

Every file stays human-readable and git-diff friendly; the journal is the
primary history interface (git history is the free audit layer on top).
Pure stdlib: the kernel (``agent/``) imports this without extra dependencies.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import secrets
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

__all__ = [
    "MAX_INDEX_ENTRIES",
    "MAX_TITLE_CHARS",
    "MEMORY_DIR",
    "MemoryEntry",
    "MemoryStore",
    "format_memory_index",
    "split_title_body",
]

#: Project-relative directory holding the memory library.
MEMORY_DIR = ".zenagent/memory"
#: Index lines injected into the system prompt (never the full text).
MAX_INDEX_ENTRIES = 64
#: One index line is the id, a space and a single-line title.
MAX_TITLE_CHARS = 80
_ID_RE = re.compile(r"^[0-9a-f]{4}$")
_NOW = "%Y-%m-%dT%H:%M:%S"

@dataclass(frozen=True)
class MemoryEntry:
    """One durable note. Timestamps are ISO strings so the file round-trips."""

    id: str
    title: str
    body: str
    created: str
    updated: str
    source: str


def _now() -> str:
    return datetime.now(UTC).strftime(_NOW)


_LAST_TS: str = ""


def _next_timestamp() -> str:
    """Second-granular timestamp, monotonic within this process.

    Two entries written in the same wall-clock second must still order
    deterministically ("newest first" in the index), so the timestamp is
    bumped by one second when the clock has not moved yet. A one-second
    skew on a rapid write burst is invisible in practice; without this the
    index order degenerates to id order on every same-second pair.
    """
    global _LAST_TS
    candidate = _now()
    if candidate <= _LAST_TS:
        candidate = _bump(_LAST_TS)
    _LAST_TS = candidate
    return candidate


def _bump(stamp: str) -> str:
    from datetime import datetime as _dt
    from datetime import timedelta as _td

    parsed = _dt.strptime(stamp, _NOW).replace(tzinfo=UTC)
    return (parsed + _td(seconds=1)).strftime(_NOW)


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split leading ``key: value`` lines (stopped by a blank line) from body.

    Tolerant by design: a file without usable frontmatter yields an empty
    dict and the full text, and the caller degrades gracefully.
    """
    fields: dict[str, str] = {}
    lines = text.split("\n")
    for index, line in enumerate(lines):
        if not line.strip():
            return fields, "\n".join(lines[index + 1 :])
        key, sep, value = line.partition(":")
        if not sep:
            return {}, text  # not frontmatter after all
        fields[key.strip()] = value.strip()
    return fields, ""


def split_title_body(text: str) -> tuple[str, str]:
    """`title: X\n\nbody` → ("X", "body"); no leading title line → ("", text)."""
    fields, body = _parse_frontmatter(text)
    return fields.get("title", ""), body


def _single_line(text: str, limit: int) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1] + "…"


class MemoryStore:
    """Read/write access to one project's memory library."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.memory_dir = self.root / MEMORY_DIR
        self.entries_dir = self.memory_dir / "entries"
        self.journal_path = self.memory_dir / "journal.jsonl"

    # -- paths -------------------------------------------------------------

    def entry_path(self, entry_id: str) -> Path:
        return self.entries_dir / f"{entry_id}.md"

    def new_id(self) -> str:
        """A fresh 4-hex id that does not collide with an existing entry."""
        for _ in range(64):
            candidate = secrets.token_hex(2)
            if not self.entry_path(candidate).exists():
                return candidate
        raise RuntimeError("memory: cannot allocate a free entry id")

    # -- entries -----------------------------------------------------------

    def read_entry(self, entry_id: str) -> MemoryEntry | None:
        if not _ID_RE.fullmatch(entry_id):
            return None  # only addressable ids exist for the store
        path = self.entry_path(entry_id)
        try:
            raw = path.read_bytes()
        except OSError:
            return None
        text = raw.decode("utf-8", "replace")
        fields, body = _parse_frontmatter(text)
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, UTC).strftime(_NOW)
        except OSError:
            mtime = _now()
        if fields:
            return MemoryEntry(
                # The filename stem is authoritative: a hand-edited frontmatter
                # id must not override the path the tools address entries by.
                id=entry_id,
                title=_single_line(str(fields.get("title", "")), MAX_TITLE_CHARS)
                or "(untitled)",
                body=body.strip(),
                created=str(fields.get("created", "")) or mtime,
                updated=str(fields.get("updated", "")) or mtime,
                source=str(fields.get("source", "")),
            )
        # Malformed / no frontmatter: degrade to first-line title, full body.
        first_line = next((line for line in text.splitlines() if line.strip()), "")
        return MemoryEntry(
            id=entry_id,
            title=_single_line(first_line, MAX_TITLE_CHARS) or "(untitled)",
            body=text,
            created=mtime,
            updated=mtime,
            source="",
        )

    def list_entries(self) -> list[MemoryEntry]:
        if not self.entries_dir.is_dir():
            return []
        # Only well-formed entry files: a stem the tools cannot address
        # (memory read/update/delete require 4-hex) must never reach the
        # prompt index — it would advertise an entry no tool can open.
        entries = [
            entry
            for path in sorted(self.entries_dir.glob("*.md"))
            if _ID_RE.fullmatch(path.stem) and (entry := self.read_entry(path.stem)) is not None
        ]
        return entries

    def write_entry(
        self,
        *,
        title: str,
        body: str,
        source: str,
        reason: str = "",
        session: str | None = None,
    ) -> MemoryEntry:
        entry_id = self.new_id()
        now = _next_timestamp()
        entry = MemoryEntry(
            id=entry_id,
            title=_single_line(title, MAX_TITLE_CHARS) or "(untitled)",
            body=body,
            created=now,
            updated=now,
            source=source,
        )
        self._write_atomically(self.entry_path(entry_id), self._render(entry))
        self.journal("add", entry_id, title=entry.title, reason=reason, session=session)
        return entry

    def update_entry(
        self,
        entry_id: str,
        *,
        title: str | None = None,
        body: str | None = None,
        reason: str = "",
        session: str | None = None,
    ) -> MemoryEntry | None:
        current = self.read_entry(entry_id)
        if current is None:
            return None
        entry = MemoryEntry(
            id=current.id,
            title=_single_line(
                title if title is not None else current.title, MAX_TITLE_CHARS
            )
            or current.title,
            body=body if body is not None else current.body,
            created=current.created,
            updated=_next_timestamp(),
            source=current.source,
        )
        self._write_atomically(self.entry_path(entry_id), self._render(entry))
        self.journal("update", entry_id, title=entry.title, reason=reason, session=session)
        return entry

    # -- journal / search / delete ----------------------------------------

    def journal(
        self,
        op: str,
        entry_id: str,
        *,
        title: str | None = None,
        reason: str = "",
        session: str | None = None,
    ) -> None:
        """Append one change event; a single write keeps concurrent appends line-atomic."""
        event = {
            "ts": _now(),
            "op": op,
            "id": entry_id,
            "title": title,
            "reason": reason,
            "session": session,
        }
        line = json.dumps(event, ensure_ascii=False) + "\n"
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        handle = os.open(self.journal_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(handle, line.encode("utf-8"))
        finally:
            os.close(handle)

    def read_journal(self) -> list[dict]:
        """Every parseable event; a torn tail line (crash mid-append) is skipped."""
        try:
            text = self.journal_path.read_text("utf-8", errors="replace")
        except OSError:
            return []
        events: list[dict] = []
        for line in text.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                events.append(event)
        return events

    def search(self, query: str) -> list[tuple[MemoryEntry, str]]:
        """Case-insensitive substring over title+body; (entry, first hit line)."""
        needle = query.strip().lower()
        if not needle:
            return []
        matches: list[tuple[MemoryEntry, str]] = []
        for entry in self.list_entries():
            haystack = f"{entry.title}\n{entry.body}".lower()
            if needle not in haystack:
                continue
            excerpt = next(
                (
                    line
                    for line in (entry.title, *entry.body.splitlines())
                    if needle in line.lower()
                ),
                entry.title,
            )
            matches.append((entry, _single_line(excerpt, 120)))
        return matches

    def delete_entry(
        self, entry_id: str, *, reason: str = "", session: str | None = None
    ) -> bool:
        if not _ID_RE.fullmatch(entry_id):
            return False
        try:
            self.entry_path(entry_id).unlink()
        except OSError:
            return False
        self.journal("delete", entry_id, reason=reason, session=session)
        return True

    # -- prompt index ------------------------------------------------------

    def format_index(self) -> str:
        """One ``- <id> <title>`` line per entry, newest first, capped.

        The system prompt carries this index — never the full text — so the
        prompt budget stays constant as the library grows; entries beyond the
        cap stay reachable through ``memory search``.
        """
        def sort_key(entry: MemoryEntry) -> tuple[str, str]:
            # The frontmatter `updated` is the semantic key and survives
            # git clone/checkout/stash (file mtime does not).
            return (entry.updated, entry.id)

        entries = sorted(self.list_entries(), key=sort_key, reverse=True)
        lines = [
            f"- {entry.id} {entry.title}" for entry in entries[:MAX_INDEX_ENTRIES]
        ]
        if not lines:
            return ""
        if len(entries) > MAX_INDEX_ENTRIES:
            lines.append(
                f"(only the {MAX_INDEX_ENTRIES} most recent entries are listed; "
                "use memory search for older ones)"
            )
        return "\n".join(lines)

    # -- rendering ---------------------------------------------------------

    @staticmethod
    def _render(entry: MemoryEntry) -> str:
        header = "\n".join(
            f"{key}: {value}"
            for key, value in (
                ("id", entry.id),
                ("title", entry.title),
                ("created", entry.created),
                ("updated", entry.updated),
                ("source", entry.source),
            )
        )
        return f"{header}\n\n{entry.body.rstrip()}\n"

    @staticmethod
    def _write_atomically(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        # The file must survive the call (delete=False) for os.replace.
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, delete=False
        ) as handle:
            try:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            except BaseException:
                handle_name = handle.name
                handle.close()
                with contextlib.suppress(OSError):
                    os.unlink(handle_name)
                raise
            handle_name = handle.name
        os.replace(handle_name, path)


def format_memory_index(store: MemoryStore) -> str:
    """Module-level wrapper so prompt code does not reach into the class."""
    return store.format_index()
