"""Session read-state: the read-before-write gate behind ``edit_file``/``write_file``.

The model may only rewrite bytes it has seen. :func:`note_read` fingerprints
every file ``read_file`` successfully returns; :func:`stale_reason` tells the
write tools why to refuse: the file was never read this session, or it changed
on disk since the read (the user, a formatter, a shell command). A successful
write refreshes the fingerprint, so follow-up edits need no re-read.

State is process-wide and deliberately not persisted: a fresh session starts
clean, and an unseen file is simply "never read". The fingerprint is
``(st_mtime_ns, st_size)`` — the same freshness signal mainstream coding
harnesses use; a same-tick, same-size rewrite can slip through, and the
edit-time exact match is the real content guard.
"""

from __future__ import annotations

from pathlib import Path

#: Resolved path -> ``(st_mtime_ns, st_size)`` as of the last read/write.
_FINGERPRINTS: dict[str, tuple[int, int]] = {}


def reset() -> None:
    """Forget every fingerprint (tests; a fresh session starts empty anyway)."""
    _FINGERPRINTS.clear()


def _key(path: Path) -> str:
    return str(path.resolve())


def _fingerprint(path: Path) -> tuple[int, int] | None:
    """The file's current ``(mtime_ns, size)``, or None when it is not there."""
    try:
        info = path.stat()
    except OSError:
        return None
    return info.st_mtime_ns, info.st_size


def note_read(path: Path) -> None:
    """Record the fingerprint as of a read that just returned these bytes."""
    fingerprint = _fingerprint(path)
    if fingerprint is not None:
        _FINGERPRINTS[_key(path)] = fingerprint


def note_write(path: Path) -> None:
    """Refresh after a successful write or edit: the model knows this content."""
    note_read(path)


def stale_reason(path: Path) -> str | None:
    """Why a write must be refused, or ``None`` when the gate passes.

    A missing file passes: ``edit_file`` reports it as not found itself, and
    ``write_file`` would be creating the file, not overwriting one.
    """
    fingerprint = _fingerprint(path)
    if fingerprint is None:
        return None
    known = _FINGERPRINTS.get(_key(path))
    if known is None:
        return "the file has not been read yet. Read it with read_file first"
    if known != fingerprint:
        return (
            "the file has been modified since it was last read (by the user, "
            "a formatter or a shell command). Read it again with read_file"
        )
    return None
