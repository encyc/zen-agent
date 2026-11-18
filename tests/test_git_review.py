"""git_review: file-level status, per-side numstat, untracked cap, diffs.

These tests drive real temporary repositories through the same shell-out
surface the RPC server uses (Task 3 wires `changes`/`diff` onto it).
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from zenagent.app import git_review
from zenagent.app.git_review import changes, diff, log

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


def _base_repo(tmp_path: Path) -> Path:
    """One committed b.txt ("x\n") as the diff fixture's HEAD."""
    repo = make_repo(tmp_path)
    write(repo / "b.txt", "x\n")
    _git(repo, "add", "b.txt")
    _git(repo, "commit", "-m", "base")
    return repo


def _body_lines(text: str) -> list[str]:
    """Diff body lines: everything after the first hunk header, minus the
    +++/--- file headers (git's `diff --git`/`index` preamble is not body)."""
    body: list[str] = []
    in_hunk = False
    for line in text.splitlines():
        if line.startswith("@@"):
            in_hunk = True
            continue
        if not in_hunk or not line or line.startswith(("+++", "---")):
            continue
        body.append(line)
    return body


def test_diff_staged_and_unstaged(tmp_path):
    repo = _base_repo(tmp_path)
    # b.txt: staged to v2 ("x\ny\n"), then edited again to v3 in the worktree.
    write(repo / "b.txt", "x\ny\n")
    _git(repo, "add", "b.txt")
    write(repo / "b.txt", "x\ny\nz\n")

    staged = diff(repo, "b.txt", staged=True)
    assert "@@" in staged["text"]
    assert staged["binary"] is False and staged["truncated"] is False and staged["missing"] is False
    assert "+y" in staged["text"] and "+z" not in staged["text"]

    unstaged = diff(repo, "b.txt")
    assert "+y" in unstaged["text"] and "+z" in unstaged["text"]


def test_diff_untracked_all_additions(tmp_path):
    repo = _base_repo(tmp_path)
    write(repo / "new.txt", "a\nb\n")
    result = diff(repo, "new.txt")
    assert result["missing"] is False
    assert "@@ -0,0 +1,2 @@" in result["text"]
    assert _body_lines(result["text"]) == ["+a", "+b"]


def test_diff_binary(tmp_path):
    repo = _base_repo(tmp_path)
    (repo / "blob.bin").write_bytes(b"\x00\x01\x02icon")
    assert diff(repo, "blob.bin")["binary"] is True


def test_diff_truncated(tmp_path):
    repo = _base_repo(tmp_path)
    write(repo / "big.txt", "".join(f"l{i}\n" for i in range(3000)))
    result = diff(repo, "big.txt")
    assert result["truncated"] is True
    assert len(result["text"].splitlines()) <= 2000


def test_diff_deleted_file(tmp_path):
    repo = _base_repo(tmp_path)
    (repo / "b.txt").unlink()
    result = diff(repo, "b.txt")
    assert result["missing"] is False
    body = _body_lines(result["text"])
    assert body and all(line.startswith("-") for line in body)


def test_diff_clean_file_is_missing(tmp_path):
    repo = _base_repo(tmp_path)
    result = diff(repo, "b.txt")
    assert result["missing"] is True
    assert result["text"] == ""


def _history_repo(tmp_path: Path) -> Path:
    """main: c1 → c2 → merge of side (c1 → s1); refs on main."""
    repo = make_repo(tmp_path)
    write(repo / "a.txt", "1\n")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-m", "c1")
    _git(repo, "checkout", "-b", "side")
    write(repo / "b.txt", "side\n")
    _git(repo, "add", "b.txt")
    _git(repo, "commit", "-m", "s1")
    _git(repo, "checkout", "main")
    write(repo / "a.txt", "1\n2\n")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-m", "c2")
    _git(repo, "merge", "--no-ff", "side", "-m", "merge side")
    return repo


def test_log_parents_and_refs(tmp_path):
    result = log(_history_repo(tmp_path))
    commits = result["commits"]
    assert len(commits) == 4
    merge = commits[0]
    assert merge["subject"] == "merge side"
    assert len(merge["parents"]) == 2
    assert all(len(parent) == 40 for parent in merge["parents"])
    assert merge["short"] == merge["hash"][:7]
    # Author comes from the environment (GIT_AUTHOR_NAME may override the
    # local config); pin non-emptiness, not the name.
    assert merge["author"] != ""
    assert merge["timestamp"] > 0
    # HEAD decorates as `HEAD -> main`; the arrow prefix is stripped.
    assert merge["refs"] == ["main"]
    # Log walks every ancestor: merge, c2, the merged s1, then c1.
    assert [c["subject"] for c in commits] == ["merge side", "c2", "s1", "c1"]
    assert commits[1]["parents"] == [commits[3]["hash"]]


def test_log_pagination(tmp_path):
    repo = _history_repo(tmp_path)
    page1 = log(repo, limit=2, skip=0)["commits"]
    page2 = log(repo, limit=2, skip=2)["commits"]
    assert [c["subject"] for c in page1] == ["merge side", "c2"]
    assert [c["subject"] for c in page2] == ["s1", "c1"]
