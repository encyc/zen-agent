"""Per-directory session sidecars: display names, pins, archive stamps.

Each sessions directory owns its sidecars (`.titles.json`, `.pins.json`,
`.archive.json`): session metadata never lives in the JSONL, so the transcript
stays append-only ``Message.to_json()`` that other tools can replay, and a
project's metadata cannot leak into another's.

All three share one contract, and it is the reason they live together:

* a missing, unreadable, corrupt, or wrong-shaped sidecar reads as *empty* —
  metadata is a convenience, so it must never break session listing or resume;
* every write replaces the file atomically (readers never see a half file);
* the setters validate their target, so "archive nothing" cannot silently
  pollute the sidecar.

The default directory resolves through :func:`zenagent.agent.session.sessions_dir`
at call time, so a test (or host) that monkeypatches it stays authoritative.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

from zenagent import project

__all__ = [
    "ARCHIVE_FILENAME",
    "PINS_FILENAME",
    "TITLES_FILENAME",
    "TITLE_LIMIT",
    "read_session_archived",
    "read_session_pins",
    "read_session_titles",
    "set_session_archived",
    "set_session_pin",
    "set_session_title",
]

#: Display names live in a sidecar keyed by session filename.
TITLES_FILENAME = ".titles.json"

#: Pinned sessions live in a sibling sidecar (a JSON array of filenames).
PINS_FILENAME = ".pins.json"

#: Archived sessions live in a sibling sidecar (filename -> archived-at epoch
#: seconds). Archiving hides a session from the sidebar without touching the
#: transcript: the JSONL path stays valid for leases, forks and live rows.
ARCHIVE_FILENAME = ".archive.json"

#: Stored display names are capped after whitespace normalization.
TITLE_LIMIT = 80


def _read_json(path: Path):
    """The sidecar's parsed payload, or ``None`` when it is not usable."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data


def _write_json_atomic(sidecar: Path, payload) -> None:
    """Replace one sidecar atomically: readers never see a half file."""
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        dir=sidecar.parent, prefix=f"{sidecar.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, sidecar)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


# -- display names ------------------------------------------------------------


def read_session_titles(directory: Path | None = None) -> dict[str, str]:
    """Stored display names by session filename, for one sessions directory.

    ``directory`` defaults to the current project's sessions directory: each
    directory owns its own sidecar, so names cannot leak between projects.
    """
    from zenagent.agent.session import sessions_dir

    base = sessions_dir() if directory is None else Path(directory)
    data = _read_json(base / TITLES_FILENAME)
    if not isinstance(data, dict):
        return {}
    return {
        name: title
        for name, title in data.items()
        if isinstance(name, str) and isinstance(title, str) and title
    }


def set_session_title(path: Path, title: str) -> str:
    """Persist ``title`` as the display name of the session file at ``path``.

    The title is whitespace-flattened and capped at :data:`TITLE_LIMIT`; a
    blank title is a caller error (there would be nothing to display), so it
    raises ``ValueError`` and leaves the sidecar untouched. The transcript is
    not read or modified.
    """
    session_file = Path(path)
    normalized = " ".join(title.split())[:TITLE_LIMIT]
    if not normalized:
        raise ValueError("session title must not be empty")
    titles = read_session_titles(session_file.parent)
    titles[session_file.name] = normalized
    _write_json_atomic(session_file.parent / TITLES_FILENAME, titles)
    return normalized


# -- pins ---------------------------------------------------------------------


def read_session_pins(directory: Path | None = None) -> set[str]:
    """Pinned session filenames for one sessions directory."""
    from zenagent.agent.session import sessions_dir

    base = sessions_dir() if directory is None else Path(directory)
    data = _read_json(base / PINS_FILENAME)
    if not isinstance(data, list):
        return set()
    return {name for name in data if isinstance(name, str) and name}


def set_session_pin(session_file: Path, pinned: bool) -> bool:
    """Pin or unpin one saved session; returns the resulting state.

    Raises ``ValueError`` for anything that is not a saved ``*.jsonl`` session
    (pinning a nonexistent file would silently pollute the sidecar). The
    transcript is never read or modified.
    """
    target = Path(session_file)
    if target.suffix != ".jsonl" or not target.is_file():
        raise ValueError(f"not a saved session file: {target}")
    pins = read_session_pins(target.parent)
    if pinned:
        pins.add(target.name)
    else:
        pins.discard(target.name)
    _write_json_atomic(target.parent / PINS_FILENAME, sorted(pins))
    return pinned


# -- archive ------------------------------------------------------------------


def read_session_archived(directory: Path | None = None) -> dict[str, float]:
    """Archived-at timestamps by session filename, for one sessions directory.

    A malformed entry is dropped rather than trusted.
    """
    from zenagent.agent.session import sessions_dir

    base = sessions_dir() if directory is None else Path(directory)
    data = _read_json(base / ARCHIVE_FILENAME)
    if not isinstance(data, dict):
        return {}
    return {
        name: float(stamp)
        for name, stamp in data.items()
        if isinstance(name, str)
        and name
        and isinstance(stamp, (int, float))
        and not isinstance(stamp, bool)
        and stamp > 0
    }


def set_session_archived(session_file: Path, archived: bool) -> float:
    """Archive or restore one saved session; returns the resulting timestamp.

    Archiving stamps ``archived_at`` into the sidecar and never touches the
    transcript; restoring drops the entry and returns ``0.0``. Raises
    ``ValueError`` for anything that is not a saved ``*.jsonl`` session, and
    for a legacy-directory file: ``~/.zenagent/sessions`` is read-only, so a
    session must be adopted into a project before it can be put away.
    """
    target = Path(session_file)
    if target.suffix != ".jsonl" or not target.is_file():
        raise ValueError(f"not a saved session file: {target}")
    try:
        if target.parent.resolve() == project.legacy_sessions_dir().resolve():
            raise ValueError(
                "legacy sessions are read-only; adopt into a project first"
            )
    except OSError:
        pass
    stamps = read_session_archived(target.parent)
    if archived:
        stamp = float(time.time())
        stamps[target.name] = stamp
        _write_json_atomic(target.parent / ARCHIVE_FILENAME, stamps)
        return stamp
    stamps.pop(target.name, None)
    _write_json_atomic(target.parent / ARCHIVE_FILENAME, stamps)
    return 0.0
