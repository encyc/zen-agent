"""Taiji snapshot builders: commit graph lanes and cross-worktree sessions."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from zenagent.app.taiji.snapshot import commit_graph


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "taiji@example.com")
    _git(repo, "config", "user.name", "taiji")
    return repo


def _commit(repo: Path, name: str, message: str) -> None:
    (repo / name).write_text(name + "\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", message)


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
class TestCommitGraph:
    def test_linear_main_stays_on_lane_zero(self, repo: Path) -> None:
        _commit(repo, "a.txt", "init")
        _commit(repo, "b.txt", "second")

        graph = commit_graph(repo)

        assert graph["is_repo"] is True
        assert [c["subject"] for c in graph["commits"]] == ["second", "init"]
        assert [c["lane"] for c in graph["commits"]] == [0, 0]
        assert graph["max_lane"] == 0
        assert graph["branches"] == [{"name": "main", "sha": graph["commits"][0]["sha"], "is_head": True}]

    def test_branch_commit_takes_new_lane_and_merge_returns(self, repo: Path) -> None:
        _commit(repo, "a.txt", "init")
        _commit(repo, "b.txt", "second")
        _git(repo, "checkout", "-qb", "topic")
        _commit(repo, "t.txt", "topic work")
        _git(repo, "checkout", "-q", "main")
        _commit(repo, "c.txt", "main work")
        _git(repo, "merge", "-q", "--no-ff", "-m", "merge topic", "topic")

        graph = commit_graph(repo)

        by_subject = {c["subject"]: c for c in graph["commits"]}
        assert by_subject["init"]["lane"] == 0
        assert by_subject["second"]["lane"] == 0
        assert by_subject["main work"]["lane"] == 0
        assert by_subject["topic work"]["lane"] == 1
        assert by_subject["merge topic"]["lane"] == 0
        assert graph["max_lane"] == 1
        merge = by_subject["merge topic"]
        assert len(merge["parents"]) == 2
        branches = {b["name"]: b for b in graph["branches"]}
        assert branches["main"]["is_head"] is True
        assert branches["topic"]["is_head"] is False
        assert branches["topic"]["sha"] == by_subject["topic work"]["sha"]

    def test_commit_fields(self, repo: Path) -> None:
        _commit(repo, "a.txt", "init")
        graph = commit_graph(repo)
        first = graph["commits"][0]
        assert set(first) == {"sha", "short", "parents", "subject", "author", "ts", "lane"}
        assert first["author"] == "taiji"
        assert isinstance(first["ts"], int)
        assert first["parents"] == []

    def test_empty_repo_yields_empty_graph(self, repo: Path) -> None:
        graph = commit_graph(repo)
        assert graph["is_repo"] is True
        assert graph["commits"] == []
        assert graph["branches"] == []
        assert graph["max_lane"] == 0

    def test_outside_a_repository(self, tmp_path: Path) -> None:
        graph = commit_graph(tmp_path)
        assert graph["is_repo"] is False
        assert graph["commits"] == []
        assert graph["branches"] == []

    def test_git_failure_yields_empty_graph(self, repo: Path, monkeypatch) -> None:
        from zenagent.app.taiji import snapshot

        monkeypatch.setattr(snapshot, "_git", lambda *args, **kwargs: None)
        graph = commit_graph(repo)
        assert graph["is_repo"] is True
        assert graph["commits"] == []
        assert graph["max_lane"] == 0
