"""The per-project taiji session singleton and its host lock.

One project has exactly one taiji session (``taiji.jsonl``) — the Router
conversation. Opening it *is* taiji mode; switching to any other session is
ordinary mode again. The lock file keeps two hosts (say two desktop windows)
from both acting as Router for the same project.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from zenagent import __version__
from zenagent.agent.session import HEADER_TYPE, RECORD_VERSION, set_session_title

TAIJI_SESSION_STEM = "taiji"
TAIJI_SESSION_TITLE = "太极 Router"
LOCK_FILENAME = "taiji.lock"


class TaijiHostLocked(RuntimeError):
    """Another live host already owns this project's taiji session."""


def taiji_session_path(sessions_dir: Path) -> Path:
    return Path(sessions_dir) / f"{TAIJI_SESSION_STEM}.jsonl"


def is_taiji_session(path: Path | None) -> bool:
    return path is not None and Path(path).stem == TAIJI_SESSION_STEM


def ensure_taiji_session(
    sessions_dir: Path, *, cwd: Path | None = None
) -> Path:
    """The taiji session file, created with its header and title on first use."""
    path = taiji_session_path(sessions_dir)
    if not path.exists() or path.stat().st_size == 0:
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "type": HEADER_TYPE,
            "version": RECORD_VERSION,
            "id": path.stem,
            "createdAt": int(time.time()),
            "cwd": str(Path(cwd).resolve()) if cwd is not None else os.getcwd(),
            "app": "zenagent",
            "appVersion": __version__,
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    set_session_title(path, TAIJI_SESSION_TITLE)
    return path


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def should_init_taiji(cwd: Path) -> bool:
    """Whether this host may materialize taiji artifacts for its project.

    Only the main checkout hosts taiji; a worker subprocess running in a
    linked worktree must not create sessions, titles or locks of its own.
    """
    from zenagent.app.workspace import worktree_overview

    overview = worktree_overview(cwd)
    return overview["is_repo"] and not overview["is_worktree"]


def _lock_path(project_dir: Path) -> Path:
    return Path(project_dir) / LOCK_FILENAME


def acquire_taiji_lock(project_dir: Path) -> None:
    """Claim this process as the project's single taiji host.

    Raises :class:`TaijiHostLocked` while another live host holds the lock;
    a lock whose pid is gone is a crashed host and is taken over.
    """
    path = _lock_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            pid = data.get("pid") if isinstance(data, dict) else None
        except (OSError, ValueError):
            pid = None
        if isinstance(pid, int) and _pid_alive(pid):
            raise TaijiHostLocked(f"taiji host pid {pid} is still running")
    descriptor = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump({"pid": os.getpid(), "createdAt": int(time.time())}, handle)


def own_taiji_lock(project_dir: Path) -> bool:
    """Acquire the host lock, reporting instead of raising.

    Returns ``True`` only when this process owns the lock; a live foreign
    host yields ``False`` and the foreign lock file is left untouched, so
    the owner's claim survives this process's exit.
    """
    try:
        acquire_taiji_lock(project_dir)
    except TaijiHostLocked:
        return False
    return True


def release_taiji_lock(project_dir: Path) -> None:
    try:
        _lock_path(project_dir).unlink()
    except OSError:
        pass
