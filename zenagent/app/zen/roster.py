"""The zen roster sidecar: which of a project's sessions are router or worker.

``.zen.json`` lives next to the session transcripts (one per project sessions
directory, same family as ``.titles.json``/``.pins.json``) so the transcript
format never changes for orchestration metadata. The file maps a session
filename stem to its role and focus; a missing or corrupt sidecar reads as an
empty roster — zen mode simply has no history yet.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

__all__ = [
    "ROSTER_FILENAME",
    "RosterEntry",
    "latest_router",
    "now_stamp",
    "read_roster",
    "set_focus",
    "set_role",
]

ROSTER_FILENAME = ".zen.json"

#: Roles the roster knows. The router is the project's interactive zen
#: session; every other tracked session is a worker with a focus.
ROLES = ("router", "worker")


@dataclass(frozen=True)
class RosterEntry:
    """One session's zen metadata (keyed by session filename stem)."""

    role: str = "worker"
    focus: str = ""


def _roster_path(sessions_dir: Path) -> Path:
    return Path(sessions_dir) / ROSTER_FILENAME


def now_stamp() -> str:
    """UTC timestamp the roster sorts recency by."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _read(sessions_dir: Path) -> dict:
    try:
        raw = json.loads(_roster_path(sessions_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _write(sessions_dir: Path, roster: dict[str, dict]) -> None:
    path = _roster_path(sessions_dir)
    payload = json.dumps(roster, ensure_ascii=False, indent=2, sort_keys=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)


def _entries(sessions_dir: Path) -> dict[str, RosterEntry]:
    return {
        stem: RosterEntry(
            role=row.get("role", "worker"),
            focus=row.get("focus", ""),
        )
        for stem, row in _read(sessions_dir).items()
        if isinstance(row, dict) and isinstance(stem, str)
    }


def read_roster(sessions_dir: Path) -> dict[str, RosterEntry]:
    """Every tracked session stem mapped to its entry; missing/corrupt → {}."""
    return _entries(sessions_dir)


def _upsert(sessions_dir: Path, stem: str, **changes: str) -> None:
    roster = _read(sessions_dir)
    row = roster.get(stem)
    base = {
        "role": row.get("role", "worker") if isinstance(row, dict) else "worker",
        "focus": row.get("focus", "") if isinstance(row, dict) else "",
        "set_at": now_stamp(),
    }
    base.update(changes)
    roster[stem] = base
    _write(sessions_dir, roster)


def set_role(sessions_dir: Path, stem: str, role: str) -> None:
    """Mark one session stem as ``role`` (see :data:`ROLES`), keeping focus."""
    if role not in ROLES:
        raise ValueError(f"unknown zen role: {role}")
    _upsert(sessions_dir, stem, role=role)


def set_focus(sessions_dir: Path, stem: str, focus: str) -> None:
    """Record what session ``stem`` concentrates on (blank clears it)."""
    _upsert(sessions_dir, stem, focus=focus)


def latest_router(sessions_dir: Path) -> str | None:
    """The most recently written router stem, or None when there is none."""
    path = _roster_path(sessions_dir)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    routers = [
        (row.get("set_at", ""), stem)
        for stem, row in raw.items()
        if isinstance(row, dict) and row.get("role") == "router"
    ]
    if not routers:
        return None
    return max(routers)[1]
