"""git_review: file-level status, per-side numstat, untracked cap, diffs.

These tests drive real temporary repositories through the same shell-out
surface the RPC server uses (Task 3 wires `changes`/`diff` onto it).
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from zenagent.app import git_review
from zenagent.app.git_review import changes

GIT = shutil.which("git")

if GIT is None:
    pytest.skip("git binary not available", allow_module_level=True)


def _git(cwd: Path, *args: str) -> None:
    assert GIT is not None
    subprocess.run(
        [GIT, *args], cwd=str(cwd), check=True, capture_output=True, text=True
    )


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def make_repo(tmp_path: Path, *setup_cmds: tuple[str, ...]) -> Path:
    """An init'd repo with a test identity; each ``setup_cmds`` tuple runs
    as one git command (e.g. ``("commit", "-m", "x")``)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@t.local")
    _git(repo, "config", "user.name", "T")
    for args in setup_cmds:
        _git(repo, *args)
    return repo


def test_changes_groups_and_numstat(tmp_path):
    repo = make_repo(tmp_path)
    write(repo / "b.txt", "x\n")
    _git(repo, "add", "b.txt")
    _git(repo, "commit", "-m", "base")
    # a.txt: staged as new (+2/-0 vs HEAD), then modified again in the worktree
    # (porcelain `AM`) — the staged/unstaged coexistence rule keeps it in the
    # staged group.
    write(repo / "a.txt", "1\n2\n")
    _git(repo, "add", "a.txt")
    write(repo / "a.txt", "1\n2\n3\n")
    # b.txt: plain unstaged edit — one line appended (+1/-0).
    write(repo / "b.txt", "x\ny\n")
    # c.txt: untracked.
    write(repo / "new.txt", "z\n")

    result = changes(repo)
    assert result["is_repo"] is True
    assert result["branch"] == "main"
    by_path = {entry["path"]: entry for entry in result["files"]}
    assert by_path["a.txt"] == {
        "path": "a.txt",
        "state": "staged",
        "plus": 2,
        "minus": 0,
    }
    assert by_path["b.txt"] == {
        "path": "b.txt",
        "state": "unstaged",
        "plus": 1,
        "minus": 0,
    }
    assert by_path["new.txt"] == {
        "path": "new.txt",
        "state": "untracked",
        "plus": None,
        "minus": None,
    }
    assert result["untracked_more"] == 0


def test_changes_untracked_cap(tmp_path):
    repo = make_repo(tmp_path)
    for index in range(205):
        (repo / f"u{index:03}.txt").write_text("x\n")

    result = changes(repo)
    untracked = [f for f in result["files"] if f["state"] == "untracked"]
    assert len(untracked) == git_review.UNTRACKED_LIMIT == 200
    assert result["untracked_more"] == 5


def test_changes_not_a_repo(tmp_path):
    result = changes(tmp_path)
    assert result == {
        "is_repo": False,
        "branch": "",
        "files": [],
        "untracked_more": 0,
    }
