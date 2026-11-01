"""git facts for the status band: porcelain parsing, caching, degradation."""

import shutil
import subprocess

import pytest

from zenagent.app import git as git_module
from zenagent.app.git import GitStatus, git_status, parse_porcelain

GIT = shutil.which("git")


def _git(cwd, *args: str) -> None:
    assert GIT is not None
    subprocess.run(
        [GIT, *args], cwd=str(cwd), check=True, capture_output=True, text=True
    )


def test_parse_branch_and_change_counts():
    text = (
        "## main...origin/main [ahead 1]\n"
        "M  staged.py\n"
        " M dirty.py\n"
        "MM both.py\n"
        "?? new.txt\n"
        "!! ignored.log\n"
    )
    assert parse_porcelain(text) == GitStatus(
        branch="main", staged=2, dirty=2, untracked=1
    )


def test_parse_detached_head_and_unborn_branch():
    assert parse_porcelain("## HEAD (no branch)\n").branch == "HEAD"
    assert parse_porcelain("## No commits yet on main\n").branch == "main"
    assert parse_porcelain("").branch == ""


def test_parse_clean_repository_has_no_marks():
    parsed = parse_porcelain("## feature/x...origin/feature/x\n")
    assert parsed.is_clean and parsed.branch == "feature/x"


def test_git_status_returns_none_outside_a_repository(tmp_path):
    git_module.reset_cache()
    assert git_status(tmp_path, ttl=0) is None


@pytest.mark.skipif(GIT is None, reason="git is not installed")
def test_git_status_reads_a_real_repository(tmp_path):
    _git(tmp_path, "init", "-b", "main")
    (tmp_path / "staged.txt").write_text("x")
    _git(tmp_path, "add", "staged.txt")
    (tmp_path / "dirty.txt").write_text("x")
    _git(tmp_path, "add", "dirty.txt")
    (tmp_path / "dirty.txt").write_text("y")  # staged, then modified again
    (tmp_path / "untracked.txt").write_text("x")

    status = git_status(tmp_path, ttl=0)
    assert status is not None
    assert status.branch == "main"
    assert status.staged >= 1
    assert status.dirty >= 1
    assert status.untracked == 1


@pytest.mark.skipif(GIT is None, reason="git is not installed")
def test_git_status_is_cached_between_repaints(tmp_path, monkeypatch):
    _git(tmp_path, "init", "-b", "main")
    calls: list[object] = []
    real = git_module._run_git

    def counting(path):
        calls.append(path)
        return real(path)

    monkeypatch.setattr(git_module, "_run_git", counting)
    first = git_status(tmp_path, ttl=60)
    second = git_status(tmp_path, ttl=60)
    assert first == second
    assert len(calls) == 1, "the status band must not spawn git on every repaint"

    # A zero TTL forces a refresh.
    git_status(tmp_path, ttl=0)
    assert len(calls) == 2
