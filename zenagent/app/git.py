"""Cheap, cached git facts for the status band.

The status band repaints far more often than a branch or a dirty count changes,
so this module shells out at most once per ``ttl`` seconds per working
directory. Everything is best-effort: no git binary, not a repository, a slow
filesystem or a malformed output all degrade to ``None`` and the segment hides
itself. Nothing here is model-facing.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

__all__ = ["GitStatus", "git_status", "parse_porcelain", "reset_cache"]

#: Seconds a parsed status stays fresh.
DEFAULT_TTL_S = 3.0

#: Hard cap on the `git status` child; a huge repo must not stall a repaint.
_TIMEOUT_S = 2.0


@dataclass(frozen=True)
class GitStatus:
    """The few facts the status band needs."""

    branch: str = ""
    #: Index changes (staged).
    staged: int = 0
    #: Working-tree changes (modified/deleted, not staged).
    dirty: int = 0
    #: Untracked files (``??``).
    untracked: int = 0

    @property
    def is_clean(self) -> bool:
        return not (self.staged or self.dirty or self.untracked)


_cache: dict[str, tuple[float, GitStatus | None]] = {}


def reset_cache() -> None:
    """Drop every cached entry (tests and ``/reload``-style resets)."""
    _cache.clear()


def parse_porcelain(text: str) -> GitStatus:
    """Parse ``git status --porcelain=v1 -b`` output.

    The first line is the branch header (``## main...origin/main [ahead 1]``);
    every other line is ``XY path``. ``X`` is the index column and ``Y`` the
    working-tree column; ``??`` means untracked and ``!!`` ignored files.
    """
    branch = ""
    staged = dirty = untracked = 0
    for line in text.splitlines():
        if line.startswith("## "):
            header = line[3:].strip()
            if header.startswith("No commits yet on "):
                branch = header[len("No commits yet on ") :]
            elif header.startswith("HEAD (no branch)"):
                branch = "HEAD"
            else:
                branch = header.split("...", 1)[0].split(" ", 1)[0]
            continue
        if len(line) < 2:
            continue
        x, y = line[0], line[1]
        if line.startswith("??"):
            untracked += 1
            continue
        if line.startswith("!!"):
            continue
        if x not in (" ", "?"):
            staged += 1
        if y not in (" ", "?"):
            dirty += 1
    return GitStatus(branch=branch, staged=staged, dirty=dirty, untracked=untracked)


def _run_git(cwd: Path) -> GitStatus | None:
    git = shutil.which("git")
    if git is None:
        return None
    try:
        completed = subprocess.run(
            [git, "status", "--porcelain=v1", "-b"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return parse_porcelain(completed.stdout)


def git_status(cwd: Path | str | None = None, *, ttl: float = DEFAULT_TTL_S) -> GitStatus | None:
    """Branch + change counts for ``cwd``, cached for ``ttl`` seconds.

    Returns ``None`` outside a repository (or without git), which makes the
    segment disappear instead of showing a stale or fake branch.
    """
    path = Path(cwd or Path.cwd())
    key = str(path)
    now = time.monotonic()
    cached = _cache.get(key)
    if cached is not None and now - cached[0] < ttl:
        return cached[1]
    result = _run_git(path)
    _cache[key] = (now, result)
    return result
