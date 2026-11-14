"""One writer per session file.

A session is append-only JSONL; two live runtimes appending to the same file
interleave turns and run boundaries. The host already avoids most duplicate
slots (``_holder``), but ``session import``, ``SessionManager`` switches and two
CLI processes can still point at one file. This module makes the invariant
explicit:

* an in-process registry keyed by the resolved path (the common case: several
  RPC slots in one host);
* an advisory file lock in ``~/.zenagent/locks/<sha256(path)>.lock`` for the
  cross-process case (two ``zen --resume`` invocations).

Read-only ``Session`` objects (transcript snapshots, export, listings) do not
participate: only a Runtime claims a lease.
"""

from __future__ import annotations

import errno

#: errno values that mean "the region is held by someone else". EDEADLOCK is
#: the POSIX alias and only exists on some platforms; getattr keeps the tuple
#: portable.
_LOCK_BUSY_ERRNOS = (
    errno.EACCES,
    errno.EAGAIN,
    errno.EWOULDBLOCK,
    errno.ENOLCK,
    errno.EDEADLK,
    getattr(errno, "EDEADLOCK", errno.EDEADLK),
)
import hashlib
import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path

from zenagent import project

__all__ = [
    "SessionLease",
    "SessionLeaseBusy",
    "acquire_session",
    "bind_session_lease",
    "ensure_session_free",
    "release_session",
    "release_session_lease",
    "session_is_live",
]

try:  # POSIX
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

try:  # Windows
    import msvcrt
except ImportError:  # pragma: no cover - POSIX
    msvcrt = None  # type: ignore[assignment]


_log = logging.getLogger(__name__)


class SessionLeaseBusy(RuntimeError):
    """Another live Runtime already owns this session file."""

    def __init__(self) -> None:
        # Deliberately path-free: RPC failure frames may echo the message.
        super().__init__("session is already open by another runtime")


@dataclass
class SessionLease:
    path: Path
    owner: object
    lock_fd: int | None = None


class _Registry:
    """The process-local half of the lease (thread-safe)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._owners: dict[Path, SessionLease] = {}

    def acquire(self, path: Path, owner: object) -> SessionLease:
        with self._lock:
            existing = self._owners.get(path)
            if existing is not None:
                if existing.owner is owner:
                    return existing
                raise SessionLeaseBusy()
            fd = _acquire_file_lock(path)
            lease = SessionLease(path=path, owner=owner, lock_fd=fd)
            self._owners[path] = lease
            return lease

    def release(self, lease: SessionLease) -> None:
        with self._lock:
            if self._owners.get(lease.path) is lease:
                self._owners.pop(lease.path, None)
            fd, lease.lock_fd = lease.lock_fd, None
        if fd is not None:
            _release_file_lock(fd)

    def owner_of(self, path: Path) -> object | None:
        with self._lock:
            lease = self._owners.get(path)
            return None if lease is None else lease.owner


_REGISTRY = _Registry()


def _resolved(path: Path | str) -> Path:
    return Path(path).expanduser().resolve()


def _lock_path(path: Path) -> Path:
    digest = hashlib.sha256(str(path).encode("utf-8")).hexdigest()
    return project.zenagent_home() / "locks" / f"{digest}.lock"


_DEGRADED_WARNED = False


def _warn_degraded(path: Path) -> None:
    """Say once, in logs, that cross-process locking is unavailable here."""
    global _DEGRADED_WARNED
    if not _DEGRADED_WARNED:
        _DEGRADED_WARNED = True
        _log.warning(
            "filesystem cannot flock (%s): session one-writer and liveness "
            "probes degrade to per-process semantics",
            path,
        )


def _acquire_file_lock(path: Path) -> int | None:
    """Best-effort advisory lock; ``None`` means the FS cannot lock.

    A lock held by someone else always raises. A filesystem that cannot create
    the lock directory/file (read-only home, unsupported FS) degrades to the
    in-process registry rather than making every session unopenable — loudly
    once, so the weakened guarantee is visible in logs (audit P2).
    """
    lock_path = _lock_path(path)
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError:
        _warn_degraded(path)
        return None
    try:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        elif msvcrt is not None:  # pragma: no cover - Windows
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"\0")
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:  # pragma: no cover - no lock primitive
            os.close(fd)
            return None
    except OSError as exc:
        os.close(fd)
        # msvcrt.locking reports a held region as EDEADLK ("resource deadlock
        # avoided"), not EACCES — without it two runtimes would both degrade
        # to unlocked instead of one refusing with SessionLeaseBusy.
        if exc.errno in _LOCK_BUSY_ERRNOS:
            raise SessionLeaseBusy() from exc
        # An unsupported filesystem should not make a session unopenable.
        _warn_degraded(path)
        return None
    return fd


def _release_file_lock(fd: int) -> None:
    try:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_UN)
        elif msvcrt is not None:  # pragma: no cover - Windows
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    except OSError:
        pass
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def acquire_session(path: Path | str, owner: object) -> SessionLease:
    """Claim ``path`` for ``owner``; raises :class:`SessionLeaseBusy` if taken."""
    return _REGISTRY.acquire(_resolved(path), owner)


def release_session(lease: SessionLease | None) -> None:
    if lease is not None:
        _REGISTRY.release(lease)


def ensure_session_free(runtime, path: Path | str | None) -> None:
    """Refuse a switch before it aborts the current run.

    ``None`` means a fresh session (the path is minted after the check), so
    there is nothing to compare.
    """
    if path is None:
        return
    resolved = _resolved(path)
    current = getattr(runtime, "_session_lease", None)
    if current is not None and current.path == resolved:
        return
    owner = _REGISTRY.owner_of(resolved)
    if owner is not None and owner is not runtime:
        raise SessionLeaseBusy()


def session_is_live(path: Path | str) -> bool:
    """Whether some live runtime currently owns ``path``.

    A read-only probe: it takes the lease for an instant and releases it. A
    filesystem that cannot lock is reported free, the same degradation the
    lease itself accepts.
    """
    resolved = _resolved(path)
    if _REGISTRY.owner_of(resolved) is not None:
        return True
    try:
        lease = acquire_session(resolved, object())
    except SessionLeaseBusy:
        return True
    # "cannot lock" is not "nobody home": reconcile must not kill a run
    # it cannot see (audit P2).
    degraded = lease.lock_fd is None
    release_session(lease)
    return degraded


def preflight_switch_lease(runtime, path: Path | str | None) -> None:
    """Take the target's lease NOW; the session setter adopts it.

    ``ensure_session_free`` only peeked the registry: a foreign process's
    flock was invisible until the setter's bind — after the current run had
    already been aborted (audit P2). Taking the lease here closes that window.
    """
    if path is None:
        return
    resolved = _resolved(path)
    current = getattr(runtime, "_session_lease", None)
    if current is not None and current.path == resolved:
        return
    stashed = getattr(runtime, "_switch_lease", None)
    if stashed is not None:
        if stashed.path == resolved:
            return
        release_session(stashed)
        runtime._switch_lease = None
    runtime._switch_lease = acquire_session(resolved, runtime)


def discard_switch_lease(runtime) -> None:
    """Release an unadopted preflight lease (switch refused or failed)."""
    stashed = getattr(runtime, "_switch_lease", None)
    if stashed is not None:
        runtime._switch_lease = None
        release_session(stashed)


def bind_session_lease(runtime, path: Path | str) -> SessionLease:
    """Acquire the new session before releasing the old one.

    Called from the ``Runtime.session`` setter. If the new path is busy this
    raises and leaves both the caller's current session and its old lease
    untouched; ``switch_session`` preflights so a run is not aborted first.
    """
    resolved = _resolved(path)
    current = getattr(runtime, "_session_lease", None)
    if current is not None and current.path == resolved:
        return current
    stashed = getattr(runtime, "_switch_lease", None)
    if stashed is not None and stashed.path == resolved:
        # switch_session preflighted this lease before aborting the run;
        # adopt it instead of re-acquiring (audit P2).
        runtime._switch_lease = None
        if current is not None:
            release_session(current)
        runtime._session_lease = stashed
        return stashed
    lease = acquire_session(resolved, runtime)
    if current is not None:
        release_session(current)
    runtime._session_lease = lease
    return lease


def release_session_lease(runtime) -> None:
    lease = getattr(runtime, "_session_lease", None)
    if lease is None:
        return
    runtime._session_lease = None
    release_session(lease)
