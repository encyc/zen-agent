"""The worktree housekeeping: the git plumbing taiji workers live and die by.

One worker = one branch = one worktree under :func:`worktrees_root`. The
sync ``_git`` runner and its ``_git_async`` off-loop wrapper live here;
``merge_gate`` borrows them, and the supervisor's async paths call the
wrapper only — one slow git must not stall the RPC pump.
"""

from __future__ import annotations

import asyncio
import re
import shutil
import subprocess
from pathlib import Path

GIT_TIMEOUT_S = 10.0
_SLUG_KEEP = re.compile(r"[^a-z0-9._-]+")
BRANCH_PREFIX = "task/"


def _slug(text: str) -> str:
    slug = _SLUG_KEEP.sub("-", text.lower()).strip("-._")
    return slug or "task"


def worktrees_root() -> Path:
    """Where worker worktrees live: outside the user's repository."""
    return Path.home() / ".zenagent" / "worktrees"


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    git = shutil.which("git")
    if git is None:
        return None
    try:
        return subprocess.run(
            [git, *args],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None


async def _git_async(root: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    """``_git`` off the event loop — async paths call this, never ``_git``.

    One slow git (a locked repo, a cold NFS mount) must not stall the RPC
    pump or the Router chat; only boot-time reconcile keeps the sync form.
    """
    return await asyncio.to_thread(_git, root, *args)


def branch_ref_exists(root: Path, name: str) -> bool:
    """Whether ``<BRANCH_PREFIX><name>`` already exists as a git ref."""
    ref = _git(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{BRANCH_PREFIX}{name}")
    return ref is not None and ref.returncode == 0


def rollback(root: Path, branch: str, worktree: str) -> None:
    """Forget a worker entirely: worktree gone, branch gone. Sync git —
    call via ``asyncio.to_thread`` from async paths."""
    _git(root, "worktree", "remove", "--force", worktree)
    shutil.rmtree(worktree, ignore_errors=True)
    _git(root, "branch", "-D", branch)


def retire_worktree(root: Path, worktree: str) -> None:
    """Remove the worker's worktree unless it still holds uncommitted work.

    Sync git — call via ``asyncio.to_thread`` from async paths.
    """
    dirty = _git(worktree, "status", "--porcelain")
    if dirty is not None and dirty.returncode == 0 and dirty.stdout.strip():
        return  # uncommitted worker output survives for inspection
    removed = _git(root, "worktree", "remove", worktree)
    if removed is None or removed.returncode != 0:
        shutil.rmtree(worktree, ignore_errors=True)
