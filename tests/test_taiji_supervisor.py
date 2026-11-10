"""The taiji supervisor: spawn with rollback, message, kill, singleton."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from zenagent.app.taiji.registry import WorkerRecord
from zenagent.app.taiji.supervisor import (
    TaijiSupervisor,
    TurnResult,
    close_taiji,
    existing_taiji,
    get_taiji,
    supervisor_summary,
)
from zenagent.project import project_key


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
        assert (tmp_path / "home" / project_key(repo) / "login-timeout").is_dir()
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
        assert not (tmp_path / "home" / project_key(repo) / "broken").exists()
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
        assert not (tmp_path / "home" / project_key(repo) / "long").exists()
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


async def test_get_taiji_keeps_one_supervisor_per_root(
    repo: Path, tmp_path: Path
) -> None:
    other = tmp_path / "other"
    other.mkdir()
    runner = StubRunner()

    def make(root: Path):
        return get_taiji(
            repo_root=root,
            sessions_dir=tmp_path / "s",
            registry_path=tmp_path / "s" / "taiji.json",
            runner=runner,
            chat=None,
            mode_of=lambda: "ask",
        )

    one = make(repo)
    again = make(repo)
    two = make(other)
    try:
        assert one is again
        assert two is not one
        assert existing_taiji(repo) is one
        assert existing_taiji(other) is two
        await close_taiji(repo)
        assert existing_taiji(repo) is None
        assert existing_taiji(other) is two
    finally:
        await close_taiji()
    assert existing_taiji() is None


async def test_existing_taiji_without_a_root_only_answers_when_unambiguous(
    repo: Path, tmp_path: Path
) -> None:
    other = tmp_path / "other"
    other.mkdir()

    def make(root: Path):
        return get_taiji(
            repo_root=root,
            sessions_dir=tmp_path / "s",
            registry_path=tmp_path / "s" / "taiji.json",
            runner=StubRunner(),
            chat=None,
            mode_of=lambda: "ask",
        )

    one = make(repo)
    try:
        assert existing_taiji() is one
        make(other)
        assert existing_taiji() is None
    finally:
        await close_taiji()


class FakeChat:
    """Records submits in order; serialized like a busy Router would be."""

    def __init__(self) -> None:
        self.messages: list[str] = []
        self._lock = asyncio.Lock()

    async def submit(self, text: str, **kwargs) -> None:
        async with self._lock:
            await asyncio.sleep(0)
            self.messages.append(text)


def make_chat_supervisor(
    repo: Path, tmp_path: Path, runner: StubRunner, *, mode: str, chat: FakeChat
) -> TaijiSupervisor:
    return TaijiSupervisor(
        repo_root=repo,
        sessions_dir=tmp_path / "sessions",
        registry_path=tmp_path / "sessions" / "taiji.json",
        runner=runner,
        chat=chat,
        mode_of=lambda: mode,
    )


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
class TestMilestonesAndMerge:
    async def test_zen_auto_merges_clean_work_and_reports_merged(
        self, repo: Path, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            "zenagent.app.taiji.supervisor.worktrees_root", lambda: tmp_path / "home"
        )
        chat = FakeChat()
        supervisor = make_chat_supervisor(
            repo, tmp_path, StubRunner(), mode="zen", chat=chat
        )

        await supervisor.spawn("clean work", name="clean")
        await supervisor.wait_idle()

        record = supervisor.list_workers()[0]
        assert record.status == "merged"
        main_log = _git(repo, "log", "--format=%s", "main").stdout
        assert "work: clean work" in main_log
        assert not (tmp_path / "home" / project_key(repo) / "clean").exists()
        assert len(chat.messages) == 1
        assert chat.messages[0].startswith("[worker task/clean · merged]")
        assert "did the thing" in chat.messages[0]

    async def test_zen_with_dirty_main_falls_back_to_acceptance_question(
        self, repo: Path, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            "zenagent.app.taiji.supervisor.worktrees_root", lambda: tmp_path / "home"
        )
        (repo / "dirty.txt").write_text("wip\n", encoding="utf-8")
        chat = FakeChat()
        supervisor = make_chat_supervisor(
            repo, tmp_path, StubRunner(), mode="zen", chat=chat
        )

        await supervisor.spawn("dirty work", name="dirty")
        await supervisor.wait_idle()

        record = supervisor.list_workers()[0]
        assert record.status == "ok"
        main_tip = _git(repo, "rev-parse", "main").stdout.strip()
        assert (
            _git(
                repo, "merge-base", "--is-ancestor", record.branch, main_tip
            ).returncode
            != 0
        )
        assert len(chat.messages) == 2
        assert chat.messages[0].startswith("[worker task/dirty · ok]")
        assert "待验收" in chat.messages[1] and "task/dirty" in chat.messages[1]

    async def test_plan_mode_never_merges_and_never_asks(
        self, repo: Path, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            "zenagent.app.taiji.supervisor.worktrees_root", lambda: tmp_path / "home"
        )
        chat = FakeChat()
        supervisor = make_chat_supervisor(
            repo, tmp_path, StubRunner(), mode="plan", chat=chat
        )

        await supervisor.spawn("planned work", name="planned")
        await supervisor.wait_idle()

        assert supervisor.list_workers()[0].status == "ok"
        assert len(chat.messages) == 1
        assert chat.messages[0].startswith("[worker task/planned · ok]")

    async def test_error_turn_reports_and_never_merges(
        self, repo: Path, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            "zenagent.app.taiji.supervisor.worktrees_root", lambda: tmp_path / "home"
        )
        chat = FakeChat()
        supervisor = make_chat_supervisor(
            repo,
            tmp_path,
            StubRunner(status="error", summary="tests failed"),
            mode="zen",
            chat=chat,
        )

        await supervisor.spawn("broken work", name="broken")
        await supervisor.wait_idle()

        assert supervisor.list_workers()[0].status == "error"
        assert len(chat.messages) == 1
        assert "[worker task/broken · error]" in chat.messages[0]
        assert "tests failed" in chat.messages[0]

    async def test_explicit_merge_works_in_ask_mode(
        self, repo: Path, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            "zenagent.app.taiji.supervisor.worktrees_root", lambda: tmp_path / "home"
        )
        chat = FakeChat()
        supervisor = make_chat_supervisor(
            repo, tmp_path, StubRunner(), mode="ask", chat=chat
        )

        await supervisor.spawn("reviewed work", name="reviewed")
        await supervisor.wait_idle()
        assert len(chat.messages) == 2  # milestone + acceptance question

        merged = await supervisor.merge("reviewed")

        assert merged is True
        assert supervisor.list_workers()[0].status == "merged"
        assert "work: reviewed work" in _git(repo, "log", "--format=%s", "main").stdout
        assert not (tmp_path / "home" / project_key(repo) / "reviewed").exists()

    async def test_summary_truncates_long_summaries(
        self, repo: Path, tmp_path: Path
    ) -> None:
        record = WorkerRecord(
            name="n",
            branch="task/n",
            base="main",
            worktree="/w",
            status="ok",
            created_at=1.0,
            updated_at=2.0,
        )
        line = supervisor_summary(record, "x" * 500, 63.0)
        assert line.startswith("[worker task/n · ok] ")
        assert line.endswith(" · 01:03")
        assert len(line) < 240


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
class TestReviewFixes:
    async def test_restart_reconciles_and_resumes(
        self, repo: Path, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            "zenagent.app.taiji.supervisor.worktrees_root", lambda: tmp_path / "home"
        )
        runner = StubRunner()
        first = make_supervisor(repo, tmp_path, runner)
        await first.spawn("long task", name="resumable")

        # a new host reconciles the on-disk registry before the first host's
        # turn completes: the worker is running on disk, so it is interrupted
        second = make_supervisor(repo, tmp_path, StubRunner())
        record = second.list_workers()[0]
        assert record.status == "interrupted"
        updated = await second.message("resumable", "continue")
        await second.wait_idle()
        assert updated.status == "ok"
        assert second.list_workers()[0].status == "ok"
        await first.wait_idle()

    async def test_git_branch_collision_gets_a_suffix_even_without_registry(
        self, repo: Path, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            "zenagent.app.taiji.supervisor.worktrees_root", lambda: tmp_path / "home"
        )
        _git(repo, "branch", "task/ghost", "main")
        supervisor = make_supervisor(repo, tmp_path, StubRunner())

        record = await supervisor.spawn("fresh work", name="ghost")
        await supervisor.wait_idle()

        assert record.branch == "task/ghost-2"

    async def test_spawn_on_a_repo_without_main_uses_the_checked_out_branch(
        self, repo: Path, tmp_path: Path, monkeypatch
    ) -> None:
        """The base is whatever the main worktree has checked out — a repo
        whose default branch is not main still works (and merges back)."""
        monkeypatch.setattr(
            "zenagent.app.taiji.supervisor.worktrees_root", lambda: tmp_path / "home"
        )
        _git(repo, "branch", "-m", "main", "master")
        supervisor = make_supervisor(repo, tmp_path, StubRunner())

        record = await supervisor.spawn("works on master", name="mastered")
        await supervisor.wait_idle()

        assert record.base == "master"
        merged = await supervisor.merge("mastered")
        assert merged is True
        assert (
            "work: works on master" in _git(repo, "log", "--format=%s", "master").stdout
        )

    async def test_merge_conflict_aborts_and_keeps_the_tree_clean(
        self, repo: Path, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            "zenagent.app.taiji.supervisor.worktrees_root", lambda: tmp_path / "home"
        )

        class TouchingRunner(StubRunner):
            async def run_turn(self, worktree, session_path, prompt):
                (Path(worktree) / "a.txt").write_text("worker side\n", encoding="utf-8")
                _git(Path(worktree), "add", ".")
                _git(Path(worktree), "commit", "-qm", "worker edit")
                return TurnResult(status="ok", summary="edited a.txt", session_path="")

        chat = FakeChat()
        supervisor = make_chat_supervisor(
            repo, tmp_path, TouchingRunner(), mode="ask", chat=chat
        )
        await supervisor.spawn("touch a.txt", name="conflict")
        await supervisor.wait_idle()
        # main moves the same file the worker moved: a guaranteed conflict
        (repo / "a.txt").write_text("main side\n", encoding="utf-8")
        _git(repo, "add", ".")
        _git(repo, "commit", "-qm", "main edit")

        merged = await supervisor.merge("conflict")

        assert merged is False
        assert _git(repo, "rev-parse", "-q", "--verify", "MERGE_HEAD").returncode != 0
        assert _git(repo, "status", "--porcelain").stdout.strip() == ""
        assert _git(repo, "merge", "--abort").returncode != 0  # nothing left to abort

    async def test_merge_keeps_a_dirty_worktree_instead_of_deleting_it(
        self, repo: Path, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            "zenagent.app.taiji.supervisor.worktrees_root", lambda: tmp_path / "home"
        )

        class MessyRunner(StubRunner):
            async def run_turn(self, worktree, session_path, prompt):
                result = await super().run_turn(worktree, session_path, prompt)
                (Path(worktree) / "notes.txt").write_text(
                    "uncommitted\n", encoding="utf-8"
                )
                return result

        supervisor = make_supervisor(repo, tmp_path, MessyRunner(), mode="ask")
        await supervisor.spawn("leave notes", name="messy")
        await supervisor.wait_idle()

        merged = await supervisor.merge("messy")

        assert merged is True
        assert (tmp_path / "home" / project_key(repo) / "messy" / "notes.txt").exists()
        assert supervisor.list_workers()[0].status == "merged"

    async def test_close_cancels_straggler_turns_quickly(
        self, repo: Path, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            "zenagent.app.taiji.supervisor.worktrees_root", lambda: tmp_path / "home"
        )

        class SlowRunner(StubRunner):
            async def run_turn(self, worktree, session_path, prompt):
                await asyncio.sleep(30)
                return TurnResult(status="ok", summary="done", session_path="")

        supervisor = make_supervisor(repo, tmp_path, SlowRunner())
        await supervisor.spawn("never finishes", name="slow")

        import time as _time

        started = _time.monotonic()
        await supervisor.close()
        elapsed = _time.monotonic() - started

        assert elapsed < 10
        assert supervisor.list_workers()[0].status == "interrupted"


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
class TestCrossReviewFixes:
    async def test_kill_cancels_the_in_flight_turn_and_stays_killed(
        self, repo: Path, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            "zenagent.app.taiji.supervisor.worktrees_root", lambda: tmp_path / "home"
        )
        cancelled = asyncio.Event()

        class SlowRunner(StubRunner):
            async def run_turn(self, worktree, session_path, prompt):
                try:
                    await asyncio.sleep(30)
                except asyncio.CancelledError:
                    cancelled.set()
                    raise
                return TurnResult(status="ok", summary="done", session_path="")

        supervisor = make_supervisor(repo, tmp_path, SlowRunner(), mode="zen")
        await supervisor.spawn("never finishes", name="racy")
        await asyncio.sleep(0.05)

        main_before = _git(repo, "rev-parse", "main").stdout.strip()
        killed = await supervisor.kill("racy")
        await asyncio.sleep(0.1)

        assert killed.status == "killed"
        assert cancelled.is_set()
        assert supervisor.list_workers()[0].status == "killed"
        # a killed task never reaches the merge gate
        assert _git(repo, "rev-parse", "main").stdout.strip() == main_before

    async def test_spawn_records_the_checked_out_base_branch(
        self, repo: Path, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            "zenagent.app.taiji.supervisor.worktrees_root", lambda: tmp_path / "home"
        )
        _git(repo, "branch", "-m", "main", "master")
        supervisor = make_supervisor(repo, tmp_path, StubRunner(), mode="ask")

        record = await supervisor.spawn("works here", name="onmaster")
        await supervisor.wait_idle()

        assert record.base == "master"
        assert record.branch == "task/onmaster"
        merged = await supervisor.merge("onmaster")
        assert merged is True
        assert "work: works here" in _git(repo, "log", "--format=%s", "master").stdout


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
class TestMergeThreshold:
    async def test_zen_small_diff_auto_merges(
        self, repo: Path, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            "zenagent.app.taiji.supervisor.worktrees_root", lambda: tmp_path / "home"
        )
        chat = FakeChat()
        supervisor = make_chat_supervisor(
            repo, tmp_path, StubRunner(), mode="zen", chat=chat
        )

        await supervisor.spawn("small change", name="small")
        await supervisor.wait_idle()

        assert supervisor.list_workers()[0].status == "merged"
        assert len(chat.messages) == 1

    async def test_zen_large_diff_falls_back_to_acceptance(
        self, repo: Path, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            "zenagent.app.taiji.supervisor.worktrees_root", lambda: tmp_path / "home"
        )

        class BigRunner(StubRunner):
            async def run_turn(self, worktree, session_path, prompt):
                (Path(worktree) / "big.txt").write_text(
                    "x" * 300 + "\n" * 300, encoding="utf-8"
                )
                _git(Path(worktree), "add", ".")
                _git(Path(worktree), "commit", "-qm", "big work")
                return TurnResult(status="ok", summary="wrote a lot", session_path="")

        chat = FakeChat()
        supervisor = make_chat_supervisor(
            repo, tmp_path, BigRunner(), mode="zen", chat=chat
        )

        await supervisor.spawn("big change", name="big")
        await supervisor.wait_idle()

        assert supervisor.list_workers()[0].status == "ok"
        main_before = _git(repo, "rev-parse", "main").stdout.strip()
        record = supervisor.list_workers()[0]
        assert (
            _git(
                repo, "merge-base", "--is-ancestor", record.branch, main_before
            ).returncode
            != 0
        )
        assert len(chat.messages) == 2
        assert "待验收" in chat.messages[1]


class GatedRunner(StubRunner):
    """Turns that start (holding a slot) and wait on a per-worker gate."""

    def __init__(self) -> None:
        super().__init__()
        self.gates: dict[str, asyncio.Event] = {}
        self.started: list[str] = []

    def gate(self, name: str) -> asyncio.Event:
        return self.gates.setdefault(name, asyncio.Event())

    async def run_turn(self, worktree, session_path, prompt):
        name = Path(worktree).name
        self.started.append(name)
        await self.gates.setdefault(name, asyncio.Event()).wait()
        return await super().run_turn(worktree, session_path, prompt)


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
class TestWorkerSlots:
    async def test_spawn_beyond_limit_queues_until_a_slot_frees(
        self, repo: Path, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            "zenagent.app.taiji.supervisor.worktrees_root", lambda: tmp_path / "home"
        )
        runner = GatedRunner()
        supervisor = TaijiSupervisor(
            repo_root=repo,
            sessions_dir=tmp_path / "sessions",
            registry_path=tmp_path / "sessions" / "taiji.json",
            runner=runner,
            chat=None,
            mode_of=lambda: "ask",
            max_workers=1,
        )

        await supervisor.spawn("first", name="job-a")
        await asyncio.sleep(0.05)
        await supervisor.spawn("second", name="job-b")
        await asyncio.sleep(0.05)

        statuses = {r.name: r.status for r in supervisor.list_workers()}
        assert statuses["job-a"] == "running"
        assert statuses["job-b"] == "queued"
        assert runner.started == ["job-a"]

        runner.gate("job-a").set()
        await asyncio.sleep(0.1)
        assert "job-b" in runner.started  # resumed as soon as the slot freed
        runner.gate("job-b").set()
        await supervisor.wait_idle()

    async def test_message_wakes_even_when_slots_are_full(
        self, repo: Path, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            "zenagent.app.taiji.supervisor.worktrees_root", lambda: tmp_path / "home"
        )
        runner = GatedRunner()
        supervisor = TaijiSupervisor(
            repo_root=repo,
            sessions_dir=tmp_path / "sessions",
            registry_path=tmp_path / "sessions" / "taiji.json",
            runner=runner,
            chat=None,
            mode_of=lambda: "ask",
            max_workers=1,
        )

        await supervisor.spawn("first", name="p1")
        await asyncio.sleep(0.05)
        runner.gate("p1").set()
        await supervisor.wait_idle()
        await supervisor.spawn("second", name="p2")
        await asyncio.sleep(0.05)  # p2 now holds the only slot, gated

        await supervisor.message("p1", "human wake")
        await asyncio.sleep(0.05)

        assert runner.started.count("p1") == 2  # woke without waiting for p2
        runner.gate("p1").set()
        runner.gate("p2").set()
        await supervisor.wait_idle()

    async def test_kill_a_queued_worker_never_runs_it(
        self, repo: Path, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            "zenagent.app.taiji.supervisor.worktrees_root", lambda: tmp_path / "home"
        )
        runner = GatedRunner()
        supervisor = TaijiSupervisor(
            repo_root=repo,
            sessions_dir=tmp_path / "sessions",
            registry_path=tmp_path / "sessions" / "taiji.json",
            runner=runner,
            chat=None,
            mode_of=lambda: "ask",
            max_workers=1,
        )

        await supervisor.spawn("first", name="holder")
        await asyncio.sleep(0.05)
        await supervisor.spawn("doomed", name="doomed")
        await asyncio.sleep(0.05)
        killed = await supervisor.kill("doomed")
        runner.gate("holder").set()
        await supervisor.wait_idle()

        assert killed.status == "killed"
        assert "doomed" not in runner.started


class AttentionRunner(StubRunner):
    """A turn that ends with the worker asking a structured question."""

    def __init__(self, attention: dict | None) -> None:
        super().__init__()
        self.attention = attention

    async def run_turn(self, worktree, session_path, prompt):
        result = await super().run_turn(worktree, session_path, prompt)
        result.attention = self.attention
        return result


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
class TestAttention:
    async def test_attention_is_persisted_and_relayed_and_blocks_merge(
        self, repo: Path, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            "zenagent.app.taiji.supervisor.worktrees_root", lambda: tmp_path / "home"
        )
        chat = FakeChat()
        supervisor = make_chat_supervisor(
            repo,
            tmp_path,
            AttentionRunner({"request_id": "q0", "question": "方案 A 还是 B？"}),
            mode="zen",
            chat=chat,
        )

        await supervisor.spawn("needs a decision", name="asking")
        await supervisor.wait_idle()

        record = supervisor.list_workers()[0]
        assert record.status == "ok"
        assert "方案 A" in record.attention
        # a pending question blocks the zen auto-merge
        main_before = _git(repo, "rev-parse", "main").stdout.strip()
        assert (
            _git(
                repo, "merge-base", "--is-ancestor", record.branch, main_before
            ).returncode
            != 0
        )
        # the Router hears the question
        assert any(
            "提问" in message and "方案 A" in message for message in chat.messages
        )

        # the human answers: the next turn clears the attention and merges
        supervisor.runner = StubRunner()
        await supervisor.message("asking", "答案：B")
        await supervisor.wait_idle()

        assert supervisor.list_workers()[0].attention == ""
        assert supervisor.list_workers()[0].status == "merged"

    async def test_registry_without_attention_field_loads(
        self, repo: Path, tmp_path: Path, monkeypatch
    ) -> None:
        import json as _json

        monkeypatch.setattr(
            "zenagent.app.taiji.supervisor.worktrees_root", lambda: tmp_path / "home"
        )
        registry_path = tmp_path / "sessions" / "taiji.json"
        registry_path.parent.mkdir(parents=True)
        registry_path.write_text(
            _json.dumps(
                {
                    "legacy": {
                        "name": "legacy",
                        "branch": "task/legacy",
                        "base": "main",
                        "worktree": "/wt",
                        "status": "interrupted",
                        "created_at": 1.0,
                        "updated_at": 2.0,
                    }
                }
            ),
            encoding="utf-8",
        )

        supervisor = make_supervisor(repo, tmp_path, StubRunner())
        assert supervisor.list_workers()[0].attention == ""


class AskingLoopback:
    """A transport whose worker asks a question mid-run and never answers."""

    def __init__(self, out: asyncio.Queue) -> None:
        self.out = out
        self._on_line = None

    async def start(self, on_line) -> None:
        self._on_line = on_line
        asyncio.create_task(self._pump())

    async def _pump(self) -> None:
        while True:
            line = await self.out.get()
            if line is None:
                return
            await self._on_line(line)

    async def send_line(self, line: str) -> None:
        payload = json.loads(line)
        if payload.get("type") == "hello":
            self.out.put_nowait(
                json.dumps(
                    {
                        "type": "result",
                        "id": payload["id"],
                        "ok": True,
                        "protocol_version": 2,
                    }
                )
            )
            return
        if payload.get("type") == "prompt":
            self.out.put_nowait(
                json.dumps(
                    {
                        "type": "event",
                        "event": {
                            "type": "ask_request",
                            "request_id": "q0",
                            "questions": [{"question": "用哪个方案？"}],
                        },
                    }
                )
            )
            return  # the worker now blocks on the ask; never completes

    async def stop(self) -> None:
        self.out.put_nowait(None)


async def test_turn_ends_promptly_when_the_worker_asks(tmp_path: Path) -> None:
    from zenagent.app.taiji.supervisor import RpcRunner
    from zenagent.app.taiji.worker_client import WorkerRpcClient

    out: asyncio.Queue = asyncio.Queue()
    runner = RpcRunner(
        client_factory=lambda: WorkerRpcClient(transport=AskingLoopback(out)),
        attention_grace_s=0.2,
    )

    started = time.time()
    result = await runner.run_turn("/tmp/wt", None, "decide something")
    elapsed = time.time() - started

    assert result.attention is not None
    assert "哪个方案" in (result.attention or {}).get("question", "")
    assert elapsed < 10


async def test_rpc_runner_routes_the_worker_model(monkeypatch) -> None:
    import zenagent.app.taiji.worker_client as worker_client_module
    from zenagent.app.taiji.supervisor import RpcRunner
    from zenagent.app.taiji.worker_client import WorkerRpcClient

    captured: dict = {}

    class CaptureTransport:
        def __init__(self, command: list[str], cwd: Path) -> None:
            captured["command"] = command

        async def start(self, on_line) -> None:
            raise RuntimeError("stop here")

        async def send_line(self, line: str) -> None:
            pass

        async def stop(self) -> None:
            pass

    monkeypatch.setattr(worker_client_module, "SubprocessTransport", CaptureTransport)
    runner = RpcRunner(worker_model="glm-5.3")
    try:
        await runner.run_turn("/tmp/wt", None, "go")
    except RuntimeError:
        pass

    assert captured["command"][-2:] == ["--model", "glm-5.3"]
    assert WorkerRpcClient is not None


async def test_message_to_a_queued_worker_cancels_the_queued_turn(repo: Path, tmp_path: Path, monkeypatch) -> None:
    """A human redirect supersedes the queued spawn turn: exactly one turn
    for that worker ever runs."""
    from zenagent.app.taiji.supervisor import TaijiSupervisor

    monkeypatch.setattr("zenagent.app.taiji.supervisor.worktrees_root", lambda: tmp_path / "home")
    runner = GatedRunner()
    supervisor = TaijiSupervisor(
        repo_root=repo,
        sessions_dir=tmp_path / "sessions",
        registry_path=tmp_path / "sessions" / "taiji.json",
        runner=runner,
        chat=None,
        mode_of=lambda: "ask",
        max_workers=1,
    )

    await supervisor.spawn("holder", name="holder")
    await asyncio.sleep(0.05)
    await supervisor.spawn("original work", name="victim")
    await asyncio.sleep(0.05)

    redirected = await supervisor.message("victim", "human redirect")
    runner.gate("holder").set()
    runner.gate("victim").set()
    await supervisor.wait_idle()

    assert redirected.status == "ok"
    assert runner.started.count("victim") == 1
    assert any(turn[2] == "human redirect" for turn in runner.turns)


async def test_explicit_merge_beats_the_auto_threshold(repo: Path, tmp_path: Path, monkeypatch) -> None:
    """The 200-line gate guards the zen AUTO merge only — the Router's
    explicit merge still works (that is the escape hatch)."""

    monkeypatch.setattr("zenagent.app.taiji.supervisor.worktrees_root", lambda: tmp_path / "home")

    class BigRunner(StubRunner):
        async def run_turn(self, worktree, session_path, prompt):
            (Path(worktree) / "big.txt").write_text("x" * 300 + "\n" * 300, encoding="utf-8")
            _git(Path(worktree), "add", ".")
            _git(Path(worktree), "commit", "-qm", "big work")
            return TurnResult(status="ok", summary="wrote a lot", session_path="")

    supervisor = make_chat_supervisor(repo, tmp_path, BigRunner(), mode="zen", chat=FakeChat())
    await supervisor.spawn("big change", name="big")
    await supervisor.wait_idle()
    assert supervisor.list_workers()[0].status == "ok"  # auto refused

    merged = await supervisor.merge("big")
    assert merged is True


class AskedThenDoneTransport:
    """Emits an ask_request, then completes the prompt normally."""

    def __init__(self, out: asyncio.Queue) -> None:
        self.out = out
        self._on_line = None

    async def start(self, on_line) -> None:
        self._on_line = on_line
        asyncio.create_task(self._pump())

    async def _pump(self) -> None:
        while True:
            line = await self.out.get()
            if line is None:
                return
            await self._on_line(line)

    async def send_line(self, line: str) -> None:
        payload = json.loads(line)
        if payload.get("type") == "hello":
            self.out.put_nowait(json.dumps({"type": "result", "id": payload["id"], "ok": True, "protocol_version": 2}))
        elif payload.get("type") == "prompt":
            self.out.put_nowait(json.dumps({
                "type": "event",
                "event": {"type": "ask_request", "request_id": "q0", "questions": [{"question": "临时一问"}]},
            }))
            self.out.put_nowait(json.dumps({
                "type": "result", "id": payload["id"], "ok": True, "text": "answered myself",
                "termination": "stop", "session": "/s.jsonl",
            }))

    async def stop(self) -> None:
        self.out.put_nowait(None)


async def test_ask_resolved_inside_the_turn_leaves_no_attention() -> None:
    from zenagent.app.taiji.supervisor import RpcRunner
    from zenagent.app.taiji.worker_client import WorkerRpcClient

    out: asyncio.Queue = asyncio.Queue()
    runner = RpcRunner(client_factory=lambda: WorkerRpcClient(transport=AskedThenDoneTransport(out)))

    result = await runner.run_turn("/tmp/wt", None, "go")

    assert result.status == "ok"
    assert result.attention is None


class ResumeFailTransport(AskedThenDoneTransport):
    async def send_line(self, line: str) -> None:
        payload = json.loads(line)
        if payload.get("type") == "session":
            self.out.put_nowait(json.dumps({"type": "error", "id": payload["id"], "message": "session file missing"}))
        elif payload.get("type") == "hello":
            self.out.put_nowait(json.dumps({"type": "result", "id": payload["id"], "ok": True, "protocol_version": 2}))
        elif payload.get("type") == "prompt":
            self.out.put_nowait(json.dumps({
                "type": "result", "id": payload["id"], "ok": True, "text": "should not run",
                "termination": "stop", "session": "/s.jsonl",
            }))


async def test_a_failed_resume_is_a_failed_turn() -> None:
    from zenagent.app.taiji.supervisor import RpcRunner
    from zenagent.app.taiji.worker_client import WorkerRpcClient

    out: asyncio.Queue = asyncio.Queue()
    runner = RpcRunner(client_factory=lambda: WorkerRpcClient(transport=ResumeFailTransport(out)))

    result = await runner.run_turn("/tmp/wt", "/missing.jsonl", "go")

    assert result.status == "error"
    assert "resume" in result.summary


# ------------------------------------------------------- inbox / slots / tracking


async def test_existing_taiji_returns_none_before_any_construction() -> None:
    from zenagent.app.taiji import supervisor as supervisor_module

    supervisor_module._SUPERVISORS.clear()
    try:
        assert supervisor_module.existing_taiji() is None
    finally:
        await supervisor_module.close_taiji()


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
class TestMessageInbox:
    async def make_gated(
        self, repo: Path, tmp_path: Path, monkeypatch, *, max_workers: int = 1
    ) -> tuple[TaijiSupervisor, GatedRunner]:
        monkeypatch.setattr(
            "zenagent.app.taiji.supervisor.worktrees_root", lambda: tmp_path / "home"
        )
        runner = GatedRunner()
        supervisor = TaijiSupervisor(
            repo_root=repo,
            sessions_dir=tmp_path / "sessions",
            registry_path=tmp_path / "sessions" / "taiji.json",
            runner=runner,
            chat=None,
            mode_of=lambda: "ask",
            max_workers=max_workers,
        )
        return supervisor, runner

    async def test_message_to_a_running_worker_queues_in_the_inbox(
        self, repo: Path, tmp_path: Path, monkeypatch
    ) -> None:
        supervisor, runner = await self.make_gated(repo, tmp_path, monkeypatch)

        await supervisor.spawn("first", name="w1")
        await asyncio.sleep(0.05)

        record = await supervisor.message("w1", "插话 A")
        await supervisor.message("w1", "插话 B")
        await asyncio.sleep(0.05)

        assert record.status == "running"  # nothing restarted while it runs
        assert runner.started.count("w1") == 1

        runner.gate("w1").set()
        await asyncio.sleep(0.1)
        assert runner.started.count("w1") == 2  # the inbox chained a follow-up turn
        assert "插话 A\n\n插话 B" in runner.turns[-1][2]
        await supervisor.wait_idle()

    async def test_kill_clears_the_inbox_and_no_followup_runs(
        self, repo: Path, tmp_path: Path, monkeypatch
    ) -> None:
        supervisor, runner = await self.make_gated(repo, tmp_path, monkeypatch)

        await supervisor.spawn("first", name="w1")
        await asyncio.sleep(0.05)
        await supervisor.message("w1", "too late")
        await supervisor.kill("w1")
        runner.gate("w1").set()
        await asyncio.sleep(0.1)
        await supervisor.wait_idle()

        assert runner.started.count("w1") == 1

    async def test_message_to_a_settled_worker_starts_immediately(
        self, repo: Path, tmp_path: Path, monkeypatch
    ) -> None:
        supervisor, runner = await self.make_gated(repo, tmp_path, monkeypatch)

        await supervisor.spawn("first", name="w1")
        runner.gate("w1").set()
        await supervisor.wait_idle()
        assert supervisor.list_workers()[0].status == "ok"

        await supervisor.message("w1", "direct answer")
        await asyncio.sleep(0.05)

        assert runner.started.count("w1") == 2
        await supervisor.wait_idle()

    async def test_message_during_settling_keeps_turn_tracking(
        self, repo: Path, tmp_path: Path, monkeypatch
    ) -> None:
        """A settling turn must only clear its own mapping, never a successor's.

        If the first turn's done-callback cleared ``_turns[name]`` after a
        chained (or human) turn already claimed the slot, a later kill would
        cancel nothing — the P2 kill race, reborn.
        """
        supervisor, runner = await self.make_gated(repo, tmp_path, monkeypatch)

        await supervisor.spawn("first", name="w1")
        await asyncio.sleep(0.05)
        live = supervisor._turns["w1"]
        sentinel = asyncio.create_task(asyncio.sleep(3600))
        supervisor._turns["w1"] = sentinel  # a newer turn claimed the slot first

        runner.gate("w1").set()
        await asyncio.gather(live, return_exceptions=True)
        await asyncio.sleep(0.05)

        assert supervisor._turns.get("w1") is sentinel
        sentinel.cancel()


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
class TestSlotsState:
    async def test_slots_state_reports_busy_max_and_queued(
        self, repo: Path, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            "zenagent.app.taiji.supervisor.worktrees_root", lambda: tmp_path / "home"
        )
        runner = GatedRunner()
        supervisor = TaijiSupervisor(
            repo_root=repo,
            sessions_dir=tmp_path / "sessions",
            registry_path=tmp_path / "sessions" / "taiji.json",
            runner=runner,
            chat=None,
            mode_of=lambda: "ask",
            max_workers=2,
        )

        await supervisor.spawn("a", name="s1")
        await asyncio.sleep(0.05)
        await supervisor.spawn("b", name="s2")
        await asyncio.sleep(0.05)
        await supervisor.spawn("c", name="s3")
        await asyncio.sleep(0.05)

        assert supervisor.slots_state() == {"busy": 2, "max": 2, "queued": 1}

        runner.gate("s1").set()
        await asyncio.sleep(0.1)
        assert supervisor.slots_state() == {"busy": 2, "max": 2, "queued": 0}

        runner.gate("s2").set()
        runner.gate("s3").set()
        await supervisor.wait_idle()
        assert supervisor.slots_state() == {"busy": 0, "max": 2, "queued": 0}


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
class TestDiffstatAndMergeReasons:
    async def make_supervisor_with_worker(
        self, repo: Path, tmp_path: Path, monkeypatch
    ) -> TaijiSupervisor:
        monkeypatch.setattr(
            "zenagent.app.taiji.supervisor.worktrees_root", lambda: tmp_path / "home"
        )
        supervisor = make_supervisor(repo, tmp_path, StubRunner())
        await supervisor.spawn("task one", name="ds")
        await supervisor.wait_idle()
        return supervisor

    async def test_diffstat_counts_added_and_deleted_lines(
        self, repo: Path, tmp_path: Path, monkeypatch
    ) -> None:
        supervisor = await self.make_supervisor_with_worker(repo, tmp_path, monkeypatch)
        record = supervisor._record("ds")
        worktree = Path(record.worktree)
        (worktree / "b.txt").write_text("x\n" * 5, encoding="utf-8")
        (worktree / "a.txt").unlink()
        _git(worktree, "add", "-A")
        _git(worktree, "commit", "-qm", "more work")

        stat = supervisor.diffstat(record)

        assert stat is not None
        by_path = {entry["path"]: entry for entry in stat}
        assert by_path["b.txt"] == {"path": "b.txt", "adds": 5, "dels": 0}
        assert by_path["a.txt"]["dels"] == 1
        assert by_path["a.txt"]["adds"] == 0

    async def test_diffstat_counts_binary_files_as_zero(
        self, repo: Path, tmp_path: Path, monkeypatch
    ) -> None:
        supervisor = await self.make_supervisor_with_worker(repo, tmp_path, monkeypatch)
        record = supervisor._record("ds")
        worktree = Path(record.worktree)
        (worktree / "blob.bin").write_bytes(b"\x00\x01\x02\xff")
        _git(worktree, "add", "-A")
        _git(worktree, "commit", "-qm", "binary")

        stat = supervisor.diffstat(record)

        assert stat is not None
        blob = next(entry for entry in stat if entry["path"] == "blob.bin")
        assert blob == {"path": "blob.bin", "adds": 0, "dels": 0}

    async def test_diffstat_returns_none_when_the_range_is_unknowable(
        self, repo: Path, tmp_path: Path, monkeypatch
    ) -> None:
        supervisor = await self.make_supervisor_with_worker(repo, tmp_path, monkeypatch)
        record = supervisor._record("ds")
        record.base = "no-such-base"

        assert supervisor.diffstat(record) is None

    async def test_merge_block_reason_reports_each_precondition(
        self, repo: Path, tmp_path: Path, monkeypatch
    ) -> None:
        from zenagent.app.git import reset_cache

        supervisor = await self.make_supervisor_with_worker(repo, tmp_path, monkeypatch)

        assert await supervisor.merge_block_reason("ghost") == "任务未处于待验收状态"
        assert await supervisor.merge_block_reason("ds") is None

        (repo / "dirty.txt").write_text("uncommitted", encoding="utf-8")
        reset_cache()  # git_status caches per directory; the mutation must be seen
        assert await supervisor.merge_block_reason("ds") == "主工作区有未提交更改"
        (repo / "dirty.txt").unlink()
        reset_cache()

        # task/ds itself is checked out by the worker worktree (git refuses a
        # second checkout), so "base elsewhere" is simulated with a new branch.
        _git(repo, "checkout", "-qb", "elsewhere")
        assert await supervisor.merge_block_reason("ds") == "基分支 main 未在主检出"
        _git(repo, "checkout", "-q", "main")


class OptionsAskLoopback(AskingLoopback):
    """The ask blocks forever, but its questions carry option labels."""

    async def send_line(self, line: str) -> None:
        payload = json.loads(line)
        if payload.get("type") == "hello":
            self.out.put_nowait(json.dumps({"type": "result", "id": payload["id"], "ok": True, "protocol_version": 2}))
        elif payload.get("type") == "prompt":
            self.out.put_nowait(json.dumps({
                "type": "event",
                "event": {"type": "ask_request", "request_id": "q0", "questions": [{
                    "question": "选哪种？",
                    "options": [
                        {"label": "方案 A"},
                        {"label": "方案 B"},
                        {"label": "方案 C"},
                        {"label": "方案 D"},
                        {"label": "方案 E"},  # past the cap: dropped
                    ],
                }]},
            }))
            return  # the worker now blocks on the ask; never completes


async def test_attention_carries_up_to_four_option_labels() -> None:
    from zenagent.app.taiji.supervisor import RpcRunner
    from zenagent.app.taiji.worker_client import WorkerRpcClient

    out: asyncio.Queue = asyncio.Queue()
    runner = RpcRunner(
        client_factory=lambda: WorkerRpcClient(transport=OptionsAskLoopback(out)),
        attention_grace_s=0.05,
    )

    result = await runner.run_turn("/tmp/wt", None, "go")

    assert result.attention is not None
    assert result.attention["question"] == "选哪种？"
    assert result.attention["options"] == ["方案 A", "方案 B", "方案 C", "方案 D"]


async def test_attention_options_stay_empty_without_labelled_choices() -> None:
    from zenagent.app.taiji.supervisor import RpcRunner
    from zenagent.app.taiji.worker_client import WorkerRpcClient

    out: asyncio.Queue = asyncio.Queue()
    runner = RpcRunner(
        client_factory=lambda: WorkerRpcClient(transport=AskingLoopback(out)),
        attention_grace_s=0.05,
    )

    result = await runner.run_turn("/tmp/wt", None, "go")

    # AskingLoopback's questions carry no options: the attention survives.
    assert result.attention is not None
    assert result.attention["options"] == []


class SlowChat:
    """A chat whose submit stalls, widening the milestone-emit window."""

    def __init__(self) -> None:
        self.released = asyncio.Event()
        self.messages: list[str] = []

    async def submit(self, text: str, **kwargs) -> None:
        await self.released.wait()
        self.messages.append(text)


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
class TestEmitWindowRace:
    async def test_message_during_emit_rides_the_next_turn_never_a_parallel_one(
        self, repo: Path, tmp_path: Path, monkeypatch
    ) -> None:
        """A message landing in the milestone-emit window must join the
        inbox of the chained turn — never start a second concurrent turn
        (the P2 kill-race shape) and never lose the earlier inbox item."""
        monkeypatch.setattr(
            "zenagent.app.taiji.supervisor.worktrees_root", lambda: tmp_path / "home"
        )
        runner = GatedRunner()
        chat = SlowChat()
        supervisor = TaijiSupervisor(
            repo_root=repo,
            sessions_dir=tmp_path / "sessions",
            registry_path=tmp_path / "sessions" / "taiji.json",
            runner=runner,
            chat=chat,
            mode_of=lambda: "ask",
            max_workers=3,
        )

        await supervisor.spawn("first", name="w1")
        await asyncio.sleep(0.05)
        await supervisor.message("w1", "M1")  # inboxed while running
        runner.gate("w1").set()  # the turn ends; the milestone emit stalls
        await asyncio.sleep(0.05)
        await supervisor.message("w1", "M2")  # lands inside the emit window
        chat.released.set()
        await asyncio.sleep(0.15)
        await supervisor.wait_idle()

        assert runner.started.count("w1") == 2  # exactly one follow-up turn
        assert "M1\n\nM2" in runner.turns[-1][2]
        assert supervisor.list_workers()[0].status == "ok"


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
async def test_git_async_offloads_to_worker_thread(monkeypatch) -> None:
    """The supervisor's async-path git helper must run off the event loop."""
    import threading

    from zenagent.app.taiji import worktree

    idents: list[int] = []

    def fake_git(root, *args):
        idents.append(threading.get_ident())

    monkeypatch.setattr(worktree, "_git", fake_git)
    await worktree._git_async(Path("/tmp"), "status")
    assert idents, "_git_async never reached git"
    assert idents[0] != threading.get_ident()


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
async def test_merge_runs_git_off_loop(repo: Path, tmp_path: Path, monkeypatch) -> None:
    """merge's whole git sequence — show-current, status, merge, retire —
    must leave the event loop; one slow git must not stall the RPC pump."""
    import subprocess
    import threading

    from zenagent.app.git import GitStatus
    from zenagent.app.taiji import supervisor as sup
    from zenagent.app.taiji import worktree

    loop_ident = threading.get_ident()
    idents: list[int] = []

    def fake_git(root, *args):
        idents.append(threading.get_ident())
        if args[:2] == ("branch", "--show-current"):
            return subprocess.CompletedProcess(args, 0, "main\n", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    def fake_status(cwd=None, **_kw):
        idents.append(threading.get_ident())
        return GitStatus(branch="main")

    monkeypatch.setattr(worktree, "_git", fake_git)
    monkeypatch.setattr(sup, "git_status", fake_status)

    supervisor = make_supervisor(repo, tmp_path, StubRunner())
    supervisor.registry.records["w1"] = WorkerRecord(
        name="w1",
        branch="task/w1",
        base="main",
        worktree=str(tmp_path / "wt"),
        status="ok",
        created_at=1.0,
        updated_at=1.0,
    )
    # Boot-time reconcile inside the constructor is sync by design (documented);
    # the assertion covers the git calls the merge itself makes.
    before_merge = len(idents)

    merged = await supervisor.merge("w1")
    assert merged is True
    assert supervisor.registry.records["w1"].status == "merged"
    merge_idents = idents[before_merge:]
    assert merge_idents, "merge never reached git"
    assert all(i != loop_ident for i in merge_idents), "git ran on the event loop"


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
class TestDurability:
    """The 2026-11-08 review: a crash before the first turn lost the task
    itself (no prompt field), and queued interjections vanished with the
    process. Both now live in the registry file."""

    async def test_inbox_survives_supervisor_restart(
        self, repo: Path, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            "zenagent.app.taiji.supervisor.worktrees_root", lambda: tmp_path / "home"
        )
        registry_path = tmp_path / "sessions" / "taiji.json"
        runner = GatedRunner()

        supervisor = TaijiSupervisor(
            repo_root=repo,
            sessions_dir=tmp_path / "sessions",
            registry_path=registry_path,
            runner=runner,
            chat=None,
            mode_of=lambda: "ask",
        )
        await supervisor.spawn("first", name="w1")
        await asyncio.sleep(0.05)
        await supervisor.message("w1", "while running")  # inboxed, not run yet
        assert runner.started.count("w1") == 1

        # The host crashes: a new supervisor loads the same registry file.
        restarted = TaijiSupervisor(
            repo_root=repo,
            sessions_dir=tmp_path / "sessions",
            registry_path=registry_path,
            runner=runner,
            chat=None,
            mode_of=lambda: "ask",
        )
        record = restarted.find_record("w1")
        assert record is not None
        # The queued word made it to disk with the status change.
        assert "while running" in "".join(record.inbox)
        # (In-process the original turn task survives; it owns the chain.
        # A real crash replays through `message`, reading this same file.)
        runner.gate("w1").set()
        await supervisor.wait_idle()
        # The chain tail carries the surviving interjection.
        assert "while running" in runner.turns[-1][2]

    async def test_spawn_persists_prompt(
        self, repo: Path, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            "zenagent.app.taiji.supervisor.worktrees_root", lambda: tmp_path / "home"
        )
        registry_path = tmp_path / "sessions" / "taiji.json"
        supervisor = TaijiSupervisor(
            repo_root=repo,
            sessions_dir=tmp_path / "sessions",
            registry_path=registry_path,
            runner=StubRunner(),
            chat=None,
            mode_of=lambda: "ask",
        )
        await supervisor.spawn("ship the release notes", name="writer")
        await supervisor.wait_idle()

        saved = json.loads(registry_path.read_text(encoding="utf-8"))
        assert saved["writer"]["prompt"] == "ship the release notes"


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
async def test_auto_merge_refuses_an_unknowable_diff(
    repo: Path, tmp_path: Path, monkeypatch
) -> None:
    """A git failure makes the diff size unknowable (-1); zen's auto merge
    must refuse rather than merge unbounded work (2026-11-08 followups #1)."""
    from zenagent.app.taiji import merge_gate

    monkeypatch.setattr(merge_gate, "diff_lines", lambda root, record: -1)
    _git(repo, "branch", "task/w1")  # the recorded branch must really exist
    supervisor = make_supervisor(repo, tmp_path, StubRunner())
    supervisor.registry.records["w1"] = WorkerRecord(
        name="w1",
        branch="task/w1",
        base="main",
        worktree=str(tmp_path / "wt"),
        status="ok",
        created_at=1.0,
        updated_at=1.0,
    )

    assert await supervisor.merge("w1", auto=True) is False
    assert supervisor.registry.records["w1"].status == "ok"
    # An explicit human merge stays the escape hatch.
    assert await supervisor.merge("w1", auto=False) is True
