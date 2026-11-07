"""Read-only taiji snapshots: the repo's commit graph and cross-worktree sessions.

Both builders shell out to git with a timeout and never raise on failure —
a slow or broken repository yields an empty snapshot, not a broken panel.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from zenagent.app.workspace import worktree_overview

GIT_TIMEOUT_S = 5.0
_LOG_SEPARATOR = "\x1f"
_LOG_FORMAT = "%H%x1f%h%x1f%P%x1f%s%x1f%an%x1f%at"
_REF_FORMAT = "%(refname:short)%1f%(objectname)"


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    import shutil

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


def _empty_graph(root: Path, *, is_repo: bool) -> dict:
    return {"root": str(root), "is_repo": is_repo, "commits": [], "branches": [], "max_lane": 0}


def _parse_log(text: str) -> list[dict]:
    commits: list[dict] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        fields = line.split(_LOG_SEPARATOR)
        if len(fields) != 6:
            continue
        sha, short, parents, subject, author, ts = fields
        commits.append(
            {
                "sha": sha,
                "short": short,
                "parents": parents.split(),
                "subject": subject,
                "author": author,
                "ts": int(ts) if ts.isdigit() else 0,
                "lane": 0,
            }
        )
    return commits


def _parse_refs(text: str) -> list[dict]:
    branches: list[dict] = []
    for line in text.splitlines():
        if _LOG_SEPARATOR not in line:
            continue
        name, sha = line.split(_LOG_SEPARATOR, 1)
        if name:
            branches.append({"name": name, "sha": sha, "is_head": False})
    return branches


def _assign_lanes(commits: list[dict]) -> int:
    """Walk newest-first, keeping one open tip per lane.

    A commit continues the lane whose tip is its first parent; a commit that is
    itself some lane's tip (a merge closing a side lane) reclaims that lane.
    Side parents keep the lane they already hold. Returns the highest lane used.
    """
    tips: list[str | None] = []
    max_lane = 0
    for commit in commits:
        parents = commit["parents"]
        lane: int | None = None
        for index, tip in enumerate(tips):
            if tip == commit["sha"]:
                lane = index
                break
        if lane is None and parents and parents[0] in tips:
            lane = tips.index(parents[0])
        if lane is None:
            lane = tips.index(None) if None in tips else len(tips)
            if lane == len(tips):
                tips.append(None)
        commit["lane"] = lane
        max_lane = max(max_lane, lane)
        tips[lane] = parents[0] if parents else None
        for parent in parents[1:]:
            if parent in tips:
                continue
            if None in tips:
                tips[tips.index(None)] = parent
            else:
                tips.append(parent)
    return max_lane


def commit_graph(cwd: Path | str, *, limit: int = 200) -> dict:
    """The repository's commit graph across all refs, with lane assignments."""
    root = Path(cwd).expanduser().resolve()
    overview = worktree_overview(root)
    if not overview["is_repo"]:
        return _empty_graph(root, is_repo=False)

    completed = _git(
        root,
        "log",
        "--all",
        "--date-order",
        "-n",
        str(limit),
        f"--pretty=format:{_LOG_FORMAT}",
    )
    commits = _parse_log(completed.stdout) if completed is not None and completed.returncode == 0 else []

    refs = _git(root, "for-each-ref", "refs/heads", f"--format={_REF_FORMAT}")
    branches = _parse_refs(refs.stdout) if refs is not None and refs.returncode == 0 else []
    heads = {wt["branch"] for wt in overview["worktrees"] if wt.get("branch")}
    for branch in branches:
        branch["is_head"] = branch["name"] in heads

    max_lane = _assign_lanes(commits)
    return {
        "root": str(root),
        "is_repo": True,
        "commits": commits,
        "branches": branches,
        "max_lane": max_lane,
    }
