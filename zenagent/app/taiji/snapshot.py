"""Read-only taiji snapshots: the repo's commit graph and cross-worktree sessions.

Both builders shell out to git with a timeout and never raise on failure —
a slow or broken repository yields an empty snapshot, not a broken panel.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from zenagent import project as project_module
from zenagent.agent.session import Session, read_session_titles
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
    return {
        "root": str(root),
        "is_repo": is_repo,
        "commits": [],
        "branches": [],
        "max_lane": 0,
    }


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
    commits = (
        _parse_log(completed.stdout)
        if completed is not None and completed.returncode == 0
        else []
    )

    refs = _git(root, "for-each-ref", "refs/heads", f"--format={_REF_FORMAT}")
    branches = (
        _parse_refs(refs.stdout) if refs is not None and refs.returncode == 0 else []
    )
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


def _worker_status(directory: Path) -> dict[str, dict]:
    """Worker records for one project directory, keyed by session file name.

    The registry is taiji's runtime state; a session that belongs to a worker
    carries that worker's status so the panel can show it without a second
    round trip.
    """
    registry_path = directory.parent / "taiji.json"
    try:
        data = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    by_session: dict[str, dict] = {}
    for raw in data.values() if isinstance(data, dict) else ():
        if not isinstance(raw, dict):
            continue
        session = raw.get("session")
        if not isinstance(session, str) or not session:
            continue
        row = {
            "worker": str(raw.get("name", "")),
            "status": str(raw.get("status", "")),
            "branch": str(raw.get("branch", "")),
        }
        attention = raw.get("attention")
        if isinstance(attention, str) and attention:
            row["attention"] = attention
        by_session[Path(session).stem] = row
    return by_session


def _list_dir_sessions(directory: Path, *, limit: int = 100) -> list[dict]:
    """Sessions of one project directory, newest first, unreadable files skipped."""
    if not directory.is_dir():
        return []
    titles = read_session_titles(directory)
    workers = _worker_status(directory)
    infos = []
    for path in sorted(directory.glob("*.jsonl")):
        try:
            infos.append(Session(path).read_info(titles=titles))
        except (ValueError, OSError):
            continue
    infos.sort(key=lambda info: info.mtime, reverse=True)
    rows = []
    for info in infos[:limit]:
        row = {
            "name": info.path.stem,
            "title": info.title,
            "updated_at": info.mtime,
            "messages": info.messages,
        }
        row.update(workers.get(info.path.stem, {}))
        rows.append(row)
    return rows


def session_map(cwd: Path | str) -> dict:
    """Zen sessions grouped by the worktree (main checkout first) they belong to."""
    root = Path(cwd).expanduser().resolve()
    overview = worktree_overview(root)
    if not overview["is_repo"]:
        return {"repo_root": str(root), "projects": []}

    main = overview["main"]
    ordered: list[tuple[str, str | None, bool]] = []
    seen: set[str] = set()
    for wt in overview["worktrees"]:
        if wt.get("bare"):
            continue
        path = wt["path"]
        if path in seen:
            continue
        seen.add(path)
        ordered.append((path, wt.get("branch") or None, path != main))
    if main not in seen:
        ordered.insert(0, (main, overview.get("branch") or None, False))

    projects_root = project_module.projects_root()
    projects = []
    for path, branch, is_linked in ordered:
        directory = projects_root / project_module.project_key(path) / "sessions"
        projects.append(
            {
                "key": project_module.project_key(path),
                "root": path,
                "branch": branch,
                "worktree": is_linked,
                "sessions": _list_dir_sessions(directory),
            }
        )
    return {"repo_root": str(root), "projects": projects}
