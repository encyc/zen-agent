"""The merge gate: deterministic diff accounting for the acceptance flow.

The LLM is never in the critical path here — these are git facts. Sync
git (``worktree._git``); callers on the event loop wrap in ``to_thread``
(the board projects off-loop for the same reason).
"""

from __future__ import annotations

from pathlib import Path

from zenagent.app.taiji.registry import WorkerRecord
from zenagent.app.taiji.worktree import _git

#: The zen *auto* merge refuses work larger than this; an explicit
#: human-directed merge is the escape hatch.
AUTO_MERGE_MAX_DIFF_LINES = 200


def diff_lines(repo_root: Path, record: WorkerRecord) -> int:
    """Added plus deleted lines between the base and the task branch."""
    completed = _git(repo_root, "diff", "--numstat", f"{record.base}...{record.branch}")
    if completed is None or completed.returncode != 0:
        return -1  # unknowable: refuse rather than guess
    total = 0
    for line in completed.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            for raw in parts[:2]:
                if raw.isdigit():
                    total += int(raw)
    return total


def compute_diffstat(repo_root: Path, record: WorkerRecord) -> list[dict] | None:
    """Per-file added/deleted counts for the acceptance card's evidence.

    Binary files (numstat's ``-``) count as zero; an unknowable diff is
    None rather than a guess.
    """
    completed = _git(repo_root, "diff", "--numstat", f"{record.base}...{record.branch}")
    if completed is None or completed.returncode != 0:
        return None
    files: list[dict] = []
    for line in completed.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3:
            files.append(
                {
                    "path": parts[2],
                    "adds": int(parts[0]) if parts[0].isdigit() else 0,
                    "dels": int(parts[1]) if parts[1].isdigit() else 0,
                }
            )
    return files


def block_reason(repo_root: Path, record: WorkerRecord) -> str | None:
    """Why a merge of ``record`` would refuse, in board-displayable words.

    Mirrors the git preconditions without executing the merge; the
    registry/status preconditions stay on the supervisor.
    """
    from zenagent.app.git import git_status

    current = _git(repo_root, "branch", "--show-current")
    if current is None or current.stdout.strip() != record.base:
        return f"基分支 {record.base} 未在主检出"
    status = git_status(repo_root)
    if status is None or not status.is_clean:
        return "主工作区有未提交更改"
    return None
