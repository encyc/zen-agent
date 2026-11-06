"""Read-only workspace inspection for the desktop shell.

The GUI's work panel browses files and shows the repository's worktrees. Both
are local, synchronous facts; nothing here is model-facing. Paths are scoped to
the project root so a client cannot walk out of the workspace, and every
operation degrades to a small error instead of raising into the RPC loop.
"""

from __future__ import annotations

import base64
import shutil
import subprocess
from pathlib import Path

from zenagent.app.git import GitStatus, git_status

__all__ = ["list_directory", "read_file", "worktree_overview"]

#: One directory listing never returns more than this many entries.
MAX_LIST_ENTRIES = 500
#: Text longer than this is truncated; the viewer can still scroll the head.
MAX_TEXT_BYTES = 512 * 1024
#: Inline image previews are bounded so one frame cannot bloat the protocol.
MAX_IMAGE_BYTES = 8 * 1024 * 1024
#: Git worktree listing is cheap but must never hang the request loop.
GIT_TIMEOUT_S = 3.0

_IMAGE_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".svg": "image/svg+xml",
}


def _resolve(root: Path, path: str | None) -> Path:
    """Resolve ``path`` inside ``root``; reject anything outside it."""
    base = root.expanduser().resolve()
    if path is None or path.strip() == "":
        target = base
    else:
        candidate = Path(path).expanduser()
        target = candidate if candidate.is_absolute() else base / candidate
        target = target.resolve()
    if target != base and base not in target.parents:
        raise ValueError(f"path escapes the workspace: {path}")
    return target


def _relative(root: Path, target: Path) -> str:
    return "" if target == root else target.relative_to(root).as_posix()


def list_directory(root: Path | str, path: str | None = "") -> dict:
    """One level of ``path`` (relative to ``root``), folders first."""
    base = Path(root).expanduser().resolve()
    target = _resolve(base, path)
    if not target.is_dir():
        raise ValueError(f"not a directory: {path or '.'}")

    entries: list[dict] = []
    truncated = False
    for entry in target.iterdir():
        if entry.name == ".git":
            continue
        if len(entries) >= MAX_LIST_ENTRIES:
            truncated = True
            break
        try:
            is_dir = entry.is_dir(follow_symlinks=False)
            size = 0 if is_dir else entry.stat(follow_symlinks=False).st_size
        except OSError:
            is_dir = False
            size = 0
        entries.append(
            {
                "name": entry.name,
                "path": _relative(base, entry),
                "kind": "dir" if is_dir else "file",
                "size": size,
                "hidden": entry.name.startswith("."),
            }
        )
    entries.sort(key=lambda item: (item["kind"] != "dir", item["name"].lower()))
    return {"path": _relative(base, target), "entries": entries, "truncated": truncated}


def read_file(root: Path | str, path: str) -> dict:
    """Text, image or binary content for one file inside the workspace."""
    base = Path(root).expanduser().resolve()
    target = _resolve(base, path)
    if target.is_dir():
        raise ValueError(f"not a file: {path}")
    if not target.is_file():
        raise ValueError(f"no such file: {path}")
    size = target.stat().st_size
    relative = _relative(base, target)

    mime = _IMAGE_MIME.get(target.suffix.lower())
    if mime is not None:
        if size > MAX_IMAGE_BYTES:
            return {"path": relative, "kind": "tooLarge", "size": size}
        data = target.read_bytes()
        return {
            "path": relative,
            "kind": "image",
            "mime": mime,
            "size": size,
            "data_url": f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}",
        }

    with target.open("rb") as handle:
        raw = handle.read(MAX_TEXT_BYTES + 1)
    truncated = len(raw) > MAX_TEXT_BYTES
    if truncated:
        raw = raw[:MAX_TEXT_BYTES]
    if b"\x00" in raw[:8192]:
        return {"path": relative, "kind": "binary", "size": size}
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return {"path": relative, "kind": "binary", "size": size}

    return {
        "path": relative,
        "kind": "text",
        "size": size,
        "text": text,
        "total_lines": len(text.splitlines()),
        "truncated": truncated,
    }


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    git = shutil.which("git")
    if git is None:
        return None
    try:
        return subprocess.run(
            [git, *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _parse_worktrees(text: str) -> list[dict]:
    """Parse ``git worktree list --porcelain`` into plain records."""
    records: list[dict] = []
    current: dict = {}
    for line in text.splitlines():
        if line.startswith("worktree "):
            if current:
                records.append(current)
            current = {"path": line[len("worktree ") :], "head": "", "branch": ""}
        elif line.startswith("HEAD ") and current:
            current["head"] = line[len("HEAD ") :]
        elif line.startswith("branch ") and current:
            branch = line[len("branch ") :]
            current["branch"] = branch.removeprefix("refs/heads/")
        elif line == "bare" and current:
            current["bare"] = True
        elif line == "detached" and current:
            current["detached"] = True
    if current:
        records.append(current)
    return records


def _status_payload(status: GitStatus | None) -> dict | None:
    if status is None:
        return None
    return {
        "branch": status.branch,
        "staged": status.staged,
        "dirty": status.dirty,
        "untracked": status.untracked,
        "clean": status.is_clean,
    }


def _contains(parent: Path, child: Path) -> bool:
    return parent == child or parent in child.parents


def worktree_overview(cwd: Path | str) -> dict:
    """The repository's worktrees plus the branch and status of ``cwd``."""
    root = Path(cwd).expanduser().resolve()
    empty = {
        "root": str(root),
        "is_repo": False,
        "is_worktree": False,
        "main": "",
        "branch": "",
        "worktrees": [],
        "status": None,
    }
    completed = _git(root, "worktree", "list", "--porcelain")
    if completed is None or completed.returncode != 0:
        return empty

    records = _parse_worktrees(completed.stdout)
    if not records:
        return empty

    worktrees = []
    current_index = -1
    for index, record in enumerate(records):
        path = Path(record["path"]).expanduser().resolve()
        is_current = path == root or _contains(path, root)
        if is_current and current_index < 0:
            current_index = index
        worktrees.append(
            {
                "path": str(path),
                "head": record["head"],
                "branch": record["branch"],
                "bare": record.get("bare", False),
                "detached": record.get("detached", False),
                "current": is_current,
            }
        )

    main = worktrees[0]["path"]
    current = worktrees[current_index] if current_index >= 0 else None
    status = git_status(root)
    return {
        "root": str(root),
        "is_repo": True,
        "is_worktree": current is not None and current["path"] != main,
        "main": main,
        "branch": current["branch"] if current is not None else (status.branch if status else ""),
        "worktrees": worktrees,
        "status": _status_payload(status),
    }
