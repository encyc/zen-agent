"""Taiji snapshot builders: commit graph lanes and cross-worktree sessions."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from zenagent.app.taiji.snapshot import commit_graph, session_map


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
        assert graph["branches"] == [
            {"name": "main", "sha": graph["commits"][0]["sha"], "is_head": True}
        ]

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
        assert set(first) == {
            "sha",
            "short",
            "parents",
            "subject",
            "author",
            "ts",
            "lane",
        }
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


def _session_file(directory: Path, stem: str, *, created_at: int = 1000) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{stem}.jsonl"
    path.write_text(
        '{"type":"session","version":1,"id":"'
        + stem
        + '","createdAt":'
        + str(created_at)
        + ',"cwd":"x","app":"zenagent","appVersion":"0"}\n',
        encoding="utf-8",
    )
    return path


class TestSessionMap:
    def test_maps_main_and_worktree_projects(
        self, repo: Path, tmp_path: Path, monkeypatch
    ) -> None:
        from zenagent.project import project_key

        _commit(repo, "a.txt", "init")
        linked = tmp_path / "linked"
        _git(repo, "worktree", "add", "-q", "-b", "feature", str(linked))
        home = tmp_path / "home"
        monkeypatch.setattr("zenagent.project.projects_root", lambda: home)

        main_dir = home / project_key(repo) / "sessions"
        _session_file(main_dir, "20260101-010101")
        (main_dir / ".titles.json").write_text(
            '{"20260101-010101.jsonl": "Router talk"}', encoding="utf-8"
        )
        _session_file(home / project_key(linked) / "sessions", "20260101-020202")

        result = session_map(repo)

        assert result["repo_root"] == str(repo.resolve())
        by_root = {p["root"]: p for p in result["projects"]}
        assert set(by_root) == {str(repo.resolve()), str(linked.resolve())}
        main_entry = by_root[str(repo.resolve())]
        assert main_entry["key"] == project_key(repo)
        assert main_entry["branch"] == "main"
        assert main_entry["worktree"] is False
        assert [s["name"] for s in main_entry["sessions"]] == ["20260101-010101"]
        assert main_entry["sessions"][0]["title"] == "Router talk"
        wt_entry = by_root[str(linked.resolve())]
        assert wt_entry["branch"] == "feature"
        assert wt_entry["worktree"] is True
        assert [s["name"] for s in wt_entry["sessions"]] == ["20260101-020202"]

    def test_sessions_sorted_newest_first(
        self, repo: Path, tmp_path: Path, monkeypatch
    ) -> None:
        from zenagent.project import project_key

        _commit(repo, "a.txt", "init")
        home = tmp_path / "home"
        monkeypatch.setattr("zenagent.project.projects_root", lambda: home)
        directory = home / project_key(repo) / "sessions"
        _session_file(directory, "older", created_at=1000)
        _session_file(directory, "newer", created_at=2000)
        import os

        os.utime(directory / "older.jsonl", (1000, 1000))
        os.utime(directory / "newer.jsonl", (2000, 2000))

        result = session_map(repo)

        assert [s["name"] for s in result["projects"][0]["sessions"]] == [
            "newer",
            "older",
        ]

    def test_worktree_without_project_dir_lists_empty_sessions(
        self, repo: Path, tmp_path: Path, monkeypatch
    ) -> None:
        _commit(repo, "a.txt", "init")
        linked = tmp_path / "linked"
        _git(repo, "worktree", "add", "-q", "-b", "feature", str(linked))
        monkeypatch.setattr("zenagent.project.projects_root", lambda: tmp_path / "home")

        result = session_map(repo)

        by_root = {p["root"]: p for p in result["projects"]}
        assert by_root[str(linked.resolve())]["sessions"] == []
        assert by_root[str(linked.resolve())]["branch"] == "feature"

    def test_detached_worktree_has_no_branch(
        self, repo: Path, tmp_path: Path, monkeypatch
    ) -> None:
        _commit(repo, "a.txt", "init")
        linked = tmp_path / "linked"
        _git(repo, "worktree", "add", "-q", "--detach", str(linked))
        monkeypatch.setattr("zenagent.project.projects_root", lambda: tmp_path / "home")

        result = session_map(repo)

        by_root = {p["root"]: p for p in result["projects"]}
        assert by_root[str(linked.resolve())]["branch"] is None

    def test_broken_titles_sidecar_is_tolerated(
        self, repo: Path, tmp_path: Path, monkeypatch
    ) -> None:
        from zenagent.project import project_key

        _commit(repo, "a.txt", "init")
        home = tmp_path / "home"
        monkeypatch.setattr("zenagent.project.projects_root", lambda: home)
        directory = home / project_key(repo) / "sessions"
        _session_file(directory, "s1")
        (directory / ".titles.json").write_text("{not json", encoding="utf-8")

        result = session_map(repo)

        assert result["projects"][0]["sessions"][0]["name"] == "s1"

    def test_outside_a_repository(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("zenagent.project.projects_root", lambda: tmp_path / "home")
        result = session_map(tmp_path)
        assert result == {"repo_root": str(tmp_path.resolve()), "projects": []}


class TestSessionStatus:
    def test_registry_status_flows_into_sessions(
        self, repo: Path, tmp_path: Path, monkeypatch
    ) -> None:
        from zenagent.app.taiji.registry import TaijiRegistry, WorkerRecord
        from zenagent.project import project_key

        _commit(repo, "a.txt", "init")
        home = tmp_path / "home"
        monkeypatch.setattr("zenagent.project.projects_root", lambda: home)
        directory = home / project_key(repo) / "sessions"
        _session_file(directory, "worker-turn")
        registry = TaijiRegistry()
        registry.records["login-timeout"] = WorkerRecord(
            name="login-timeout",
            branch="task/login-timeout",
            base="main",
            worktree="/wt",
            status="running",
            created_at=1.0,
            updated_at=2.0,
            session=str(directory / "worker-turn.jsonl"),
        )
        registry.save(directory.parent / "taiji.json")

        result = session_map(repo)

        sessions = result["projects"][0]["sessions"]
        assert sessions[0]["status"] == "running"
        assert sessions[0]["worker"] == "login-timeout"
        assert sessions[0]["branch"] == "task/login-timeout"

    def test_sessions_without_a_worker_have_no_status(
        self, repo: Path, tmp_path: Path, monkeypatch
    ) -> None:
        from zenagent.project import project_key

        _commit(repo, "a.txt", "init")
        home = tmp_path / "home"
        monkeypatch.setattr("zenagent.project.projects_root", lambda: home)
        _session_file(home / project_key(repo) / "sessions", "plain")

        result = session_map(repo)

        session = result["projects"][0]["sessions"][0]
        assert "status" not in session
        assert "worker" not in session


class TestAttentionSnapshot:
    def test_worker_attention_surfaces_in_the_session_row(
        self, repo: Path, tmp_path: Path, monkeypatch
    ) -> None:
        from zenagent.app.taiji.registry import TaijiRegistry, WorkerRecord
        from zenagent.project import project_key

        _commit(repo, "a.txt", "init")
        home = tmp_path / "home"
        monkeypatch.setattr("zenagent.project.projects_root", lambda: home)
        directory = home / project_key(repo) / "sessions"
        _session_file(directory, "worker-turn")
        registry = TaijiRegistry()
        registry.records["w"] = WorkerRecord(
            name="w",
            branch="task/w",
            base="main",
            worktree="/wt",
            status="ok",
            created_at=1.0,
            updated_at=2.0,
            session=str(directory / "worker-turn.jsonl"),
            attention='{"request_id": "q0", "question": "A 还是 B？"}',
        )
        registry.save(directory.parent / "taiji.json")

        result = session_map(repo)

        session = result["projects"][0]["sessions"][0]
        assert session["status"] == "ok"
        assert "A 还是 B" in session["attention"]
