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

__all__ = ["UNTRACKED_LIMIT", "DIFF_LINE_LIMIT", "changes", "diff", "log"]

#: Hard cap on every git child; a huge repo must not stall a panel refresh.
_TIMEOUT_S = 5.0

#: Untracked files listed before the panel folds the rest into a count.
UNTRACKED_LIMIT = 200

#: Total diff lines served for one file; beyond this the payload is cut and
#: flagged, and the panel links to the Files viewer for the rest.
DIFF_LINE_LIMIT = 2000

#: Bytes sniffed for a NUL when deciding an untracked file is binary.
_BINARY_SNIFF_BYTES = 8192


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


def _tracked(cwd: Path, path: str) -> bool:
    completed = _run_git(cwd, "ls-files", "--", path)
    return (
        completed is not None
        and completed.returncode == 0
        and completed.stdout.strip() != ""
    )


def _truncate(text: str) -> tuple[str, bool]:
    lines = text.split("\n")
    if len(lines) <= DIFF_LINE_LIMIT:
        return text, False
    return "\n".join(lines[:DIFF_LINE_LIMIT]), True


def _all_additions(target: Path, path: str) -> dict:
    """A synthetic whole-file-is-new diff for an untracked path."""
    try:
        raw = target.read_bytes()
    except OSError:
        return {"text": "", "binary": False, "truncated": False, "missing": True}
    if b"\0" in raw[:_BINARY_SNIFF_BYTES]:
        return {"text": "", "binary": True, "truncated": False, "missing": False}
    lines = raw.decode("utf-8", errors="replace").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if not lines:
        return {"text": "", "binary": False, "truncated": False, "missing": False}
    body = lines[: DIFF_LINE_LIMIT - 3]
    truncated = len(lines) > DIFF_LINE_LIMIT - 3
    text = (
        f"--- /dev/null\n+++ {path}\n@@ -0,0 +1,{len(body)} @@\n"
        + "\n".join("+" + line for line in body)
        + "\n"
    )
    return {"text": text, "binary": False, "truncated": truncated, "missing": False}


def diff(cwd: Path | str, path: str, *, staged: bool = False) -> dict:
    """One file's unified diff: ``{"text", "binary", "truncated", "missing"}``.

    ``staged`` diffs the index (``--cached``); a tracked path diffs the work
    tree against HEAD; an untracked path gets a synthetic all-additions diff.
    A clean (or vanished) path is ``missing`` with empty text.
    """
    root = Path(cwd).expanduser().resolve()
    if staged:
        completed = _run_git(root, "diff", "--cached", "--no-renames", "--", path)
        text = completed.stdout if completed is not None and completed.returncode == 0 else ""
    elif _tracked(root, path):
        completed = _run_git(root, "diff", "HEAD", "--no-renames", "--", path)
        text = completed.stdout if completed is not None and completed.returncode == 0 else ""
    else:
        return _all_additions(root / path, path)

    if text == "":
        return {"text": "", "binary": False, "truncated": False, "missing": True}
    if "Binary files " in text and " differ" in text:
        return {"text": "", "binary": True, "truncated": False, "missing": False}
    text, truncated = _truncate(text)
    return {"text": text, "binary": False, "truncated": truncated, "missing": False}


def log(cwd: Path | str, *, limit: int = 100, skip: int = 0) -> dict:
    """The commit DAG page the graph renders: full hashes and parents (lane
    layout needs them), plus subject/author/date and `%D` ref decorations with
    the ``HEAD ->`` arrow stripped."""
    root = Path(cwd).expanduser().resolve()
    completed = _run_git(
        root,
        "log",
        f"--skip={max(0, skip)}",
        f"-n{max(1, limit)}",
        "--pretty=format:%H%x1f%h%x1f%P%x1f%s%x1f%an%x1f%at%x1f%D%x1e",
    )
    if completed is None or completed.returncode != 0:
        return {"commits": []}
    commits = []
    for record in completed.stdout.split("\x1e"):
        fields = [field.strip() for field in record.split("\x1f")]
        if len(fields) < 6 or fields[0] == "":
            continue
        refs = [
            ref.strip().removeprefix("HEAD -> ")
            for ref in fields[6].split(", ")
            if ref.strip() and ref.strip() != "HEAD"
        ]
        commits.append(
            {
                "hash": fields[0],
                "short": fields[1],
                "parents": fields[2].split() if fields[2] else [],
                "subject": fields[3],
                "author": fields[4],
                "timestamp": int(fields[5]) if fields[5].isdigit() else 0,
                "refs": refs,
            }
        )
    return {"commits": commits}


def show(cwd: Path | str, commit: str) -> dict:
    """One commit's diff against its first parent (the root commit against
    the empty tree): metadata from a `show -s`, per-file unified bodies from
    an explicit two-endpoint diff, counts derived from the body itself. A
    text-empty merge stays ``found`` with no files."""
    missing = {"found": False, "short": "", "subject": "", "files": []}
    root = Path(cwd).expanduser().resolve()
    meta = _run_git(root, "show", "-s", "--format=%H%x1f%h%x1f%s%x1f%P", commit)
    if meta is None or meta.returncode != 0:
        return missing
    fields = meta.stdout.rstrip("\n").split("\x1f")
    if len(fields) < 3 or fields[0] == "":
        return missing
    # A root commit has no parents, so the trailing %P is empty and git may
    # leave no separator at all: a short split means "no parents", not "not
    # found".
    short, subject = fields[1], fields[2]
    parents = fields[3].split() if len(fields) > 3 else []
    if parents:
        body = _run_git(root, "diff", "--no-renames", "-p", parents[0], commit)
    else:
        body = _run_git(root, "diff-tree", "--root", "--no-renames", "-p", "-r", commit)

    files: list[dict] = []
    if body is not None and body.returncode == 0 and body.stdout != "":
        chunks: list[str] = []
        for line in body.stdout.split("\n"):
            if line.startswith("diff --git "):
                chunks.append(line)
            elif chunks:
                chunks[-1] += "\n" + line
        for chunk in chunks:
            lines = chunk.split("\n")[1:]
            additions = sum(1 for l in lines if l.startswith("+") and not l.startswith("+++"))
            removals = sum(1 for l in lines if l.startswith("-") and not l.startswith("---"))
            text, _ = _truncate(chunk)
            files.append(
                {
                    "path": _path_of_show_chunk(chunk),
                    "additions": additions,
                    "removals": removals,
                    "diff": text,
                }
            )
    return {"found": True, "short": short, "subject": subject, "files": files}


def _path_of_show_chunk(chunk: str) -> str:
    """`diff --git a/x b/x` → `x`."""
    first = chunk.split("\n", 1)[0]
    parts = first.split(" b/", 1)
    if len(parts) == 2 and parts[1] != "":
        return parts[1].strip('"')
    return first
