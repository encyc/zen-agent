"""Read-only git facts for the desktop review panel.

Everything here backs the Git tab's changes list and diff detail: one shell-out
per fact, best-effort, no writes and no model-facing surface. Like ``app/git``
everything degrades to an empty payload rather than raising — the panel shows
a placeholder, never a traceback. Commands run with ``--no-renames`` so a path
is one stable key across status, numstat and diff.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

__all__ = ["UNTRACKED_LIMIT", "changes"]

#: Hard cap on every git child; a huge repo must not stall a panel refresh.
_TIMEOUT_S = 5.0

#: Untracked files listed before the panel folds the rest into a count.
UNTRACKED_LIMIT = 200


def _run_git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    git = shutil.which("git")
    if git is None:
        return None
    try:
        return subprocess.run(
            [git, *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _numstat(cwd: Path, *args: str) -> dict[str, tuple[int, int]]:
    """``git diff --numstat`` output as ``{path: (plus, minus)}``; binary
    rows (``-`` counts) record zeros."""
    completed = _run_git(cwd, "diff", "--no-renames", "--numstat", *args)
    if completed is None or completed.returncode != 0:
        return {}
    counts: dict[str, tuple[int, int]] = {}
    for line in completed.stdout.splitlines():
        if not line:
            continue
        plus, _, rest = line.partition("\t")
        minus, _, path = rest.partition("\t")
        if path == "":
            continue
        counts[path] = (
            int(plus) if plus.isdigit() else 0,
            int(minus) if minus.isdigit() else 0,
        )
    return counts


def _branch_of(status_text: str) -> str:
    """The branch from a ``--porcelain=v1 -b`` header, with the same
    unborn/detached handling as ``app/git.parse_porcelain``."""
    for line in status_text.splitlines():
        if not line.startswith("## "):
            continue
        header = line[3:].strip()
        if header.startswith("No commits yet on "):
            return header[len("No commits yet on ") :]
        if header.startswith("HEAD (no branch)"):
            return "HEAD"
        return header.split("...", 1)[0].split(" ", 1)[0]
    return ""


def changes(cwd: Path | str) -> dict:
    """File-level working-tree state for the changes list.

    ``{"is_repo", "branch", "files", "untracked_more"}`` where each file is
    ``{"path", "state", "plus", "minus"}``. State follows the porcelain XY
    columns: index column set → staged (a row staged *and* unstaged counts as
    staged), work-tree column set → unstaged, ``??`` → untracked (counts are
    None — numstat has nothing to say about untracked files). Untracked rows
    are capped at ``UNTRACKED_LIMIT``; the surplus lands in ``untracked_more``.
    """
    root = Path(cwd).expanduser().resolve()
    empty = {"is_repo": False, "branch": "", "files": [], "untracked_more": 0}
    completed = _run_git(root, "status", "--porcelain=v1", "-b", "-uall")
    if completed is None or completed.returncode != 0:
        return empty

    staged_counts = _numstat(root, "--cached")
    unstaged_counts = _numstat(root)
    files: list[dict] = []
    untracked_more = 0
    for line in completed.stdout.splitlines():
        if line.startswith("## ") or len(line) < 4:
            continue
        x, y = line[0], line[1]
        path = line[3:]
        if line.startswith("??"):
            if len([f for f in files if f["state"] == "untracked"]) >= UNTRACKED_LIMIT:
                untracked_more += 1
            else:
                files.append(
                    {"path": path, "state": "untracked", "plus": None, "minus": None}
                )
            continue
        if x not in (" ", "?"):
            plus, minus = staged_counts.get(path, (0, 0))
            files.append({"path": path, "state": "staged", "plus": plus, "minus": minus})
        elif y not in (" ", "?"):
            plus, minus = unstaged_counts.get(path, (0, 0))
            files.append(
                {"path": path, "state": "unstaged", "plus": plus, "minus": minus}
            )
    return {
        "is_repo": True,
        "branch": _branch_of(completed.stdout),
        "files": files,
        "untracked_more": untracked_more,
    }
