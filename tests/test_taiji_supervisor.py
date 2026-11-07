"""The taiji supervisor: spawn with rollback, message, kill, singleton."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from zenagent.app.taiji.registry import WorkerRecord
from zenagent.app.taiji.supervisor import (
    TaijiSupervisor,
    TurnResult,
    close_taiji,
    get_taiji,
)


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=False
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "taiji@example.com")
    _git(repo, "config", "user.name", "taiji")
    (repo / "a.txt").write_text("one\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "init")
    return repo


def branch_exists(repo: Path, branch: str) -> bool:
    return _git(repo, "rev-parse", "--verify", f"refs/heads/{branch}").returncode == 0


class StubRunner:
    """Turns that optionally commit into the worktree, or fail at startup."""

    def __init__(
        self,
        *,
        status: str = "ok",
        summary: str = "did the thing",
        fail_start: bool = False,
    ) -> None:
        self.status = status
        self.summary = summary
        self.fail_start = fail_start
        self.turns: list[tuple[str, str | None, str]] = []

    async def run_turn(
        self, worktree: str, session_path: str | None, prompt: str
    ) -> TurnResult:
        self.turns.append((worktree, session_path, prompt))
        if self.fail_start:
            raise RuntimeError("worker never started")
        (Path(worktree) / "work.txt").write_text(prompt, encoding="utf-8")
        _git(Path(worktree), "add", ".")
        _git(Path(worktree), "commit", "-qm", f"work: {prompt[:20]}")
        return TurnResult(
            status=self.status,
            summary=self.summary,
            session_path=str(Path(worktree) / "w.jsonl"),
        )


def make_supervisor(
    repo: Path, tmp_path: Path, runner: StubRunner, *, mode: str = "ask"
) -> TaijiSupervisor:
    return TaijiSupervisor(
        repo_root=repo,
        sessions_dir=tmp_path / "sessions",
        registry_path=tmp_path / "sessions" / "taiji.json",
        runner=runner,
        chat=None,
        mode_of=lambda: mode,
    )


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
class TestSupervisor:
    async def test_spawn_creates_branch_worktree_and_record(
        self, repo: Path, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            "zenagent.app.taiji.supervisor.worktrees_root", lambda: tmp_path / "home"
        )
        runner = StubRunner()
        supervisor = make_supervisor(repo, tmp_path, runner)

        record = await supervisor.spawn("fix the login timeout", name="login-timeout")
        await supervisor.wait_idle()

        assert record.branch == "task/login-timeout"
        assert record.status in ("running", "ok")
        assert branch_exists(repo, "task/login-timeout")
        assert (tmp_path / "home" / "login-timeout").is_dir()
        assert supervisor.list_workers()[0].name == "login-timeout"
        assert runner.turns[0][2] == "fix the login timeout"
        assert (tmp_path / "sessions" / "taiji.json").exists()

    async def test_spawn_name_collision_gets_a_suffix(
        self, repo: Path, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            "zenagent.app.taiji.supervisor.worktrees_root", lambda: tmp_path / "home"
        )
        supervisor = make_supervisor(repo, tmp_path, StubRunner())

        first = await supervisor.spawn("one", name="dupe")
        second = await supervisor.spawn("two", name="dupe")
        await supervisor.wait_idle()

        assert first.branch == "task/dupe"
        assert second.branch == "task/dupe-2"
        assert second.name == "dupe-2"

    async def test_startup_failure_rolls_back(
        self, repo: Path, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            "zenagent.app.taiji.supervisor.worktrees_root", lambda: tmp_path / "home"
        )
        supervisor = make_supervisor(repo, tmp_path, StubRunner(fail_start=True))

        record = await supervisor.spawn("will fail", name="broken")
        await supervisor.wait_idle()

        assert not branch_exists(repo, "task/broken")
        assert not (tmp_path / "home" / "broken").exists()
        assert supervisor.list_workers() == []
        assert record.name == "broken"

    async def test_kill_removes_worktree_keeps_branch(
        self, repo: Path, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            "zenagent.app.taiji.supervisor.worktrees_root", lambda: tmp_path / "home"
        )
        supervisor = make_supervisor(repo, tmp_path, StubRunner())

        await supervisor.spawn("long running", name="long")
        killed = await supervisor.kill("long")

        assert killed.status == "killed"
        assert not (tmp_path / "home" / "long").exists()
        assert branch_exists(repo, "task/long")
        assert supervisor.list_workers()[0].status == "killed"

    async def test_message_resumes_the_worker_session(
        self, repo: Path, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            "zenagent.app.taiji.supervisor.worktrees_root", lambda: tmp_path / "home"
        )
        runner = StubRunner()
        supervisor = make_supervisor(repo, tmp_path, runner)

        await supervisor.spawn("first turn", name="resumed")
        await supervisor.wait_idle()
        updated = await supervisor.message("resumed", "second turn")
        await supervisor.wait_idle()

        assert updated.status == "ok"
        assert runner.turns[0][1] is None
        assert runner.turns[1][2] == "second turn"
        assert (
            runner.turns[1][1] is not None
            and Path(runner.turns[1][1]).name == "w.jsonl"
        )

    async def test_unknown_worker_message_raises(
        self, repo: Path, tmp_path: Path
    ) -> None:
        supervisor = make_supervisor(repo, tmp_path, StubRunner())
        with pytest.raises(KeyError):
            await supervisor.message("ghost", "hi")


async def test_get_taiji_is_a_per_loop_singleton(repo: Path, tmp_path: Path) -> None:
    runner = StubRunner()
    one = get_taiji(
        repo_root=repo,
        sessions_dir=tmp_path / "s",
        registry_path=tmp_path / "s" / "taiji.json",
        runner=runner,
        chat=None,
        mode_of=lambda: "ask",
    )
    two = get_taiji(
        repo_root=repo,
        sessions_dir=tmp_path / "s",
        registry_path=tmp_path / "s" / "taiji.json",
        runner=runner,
        chat=None,
        mode_of=lambda: "ask",
    )
    assert one is two
    await close_taiji()
