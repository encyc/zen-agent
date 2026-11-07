"""The taiji supervisor: spawn with rollback, message, kill, singleton."""

from __future__ import annotations

import asyncio
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
        assert "work: works on master" in _git(repo, "log", "--format=%s", "master").stdout

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
    async def test_kill_cancels_the_in_flight_turn_and_stays_killed(self, repo: Path, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("zenagent.app.taiji.supervisor.worktrees_root", lambda: tmp_path / "home")
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

    async def test_spawn_records_the_checked_out_base_branch(self, repo: Path, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("zenagent.app.taiji.supervisor.worktrees_root", lambda: tmp_path / "home")
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
    async def test_zen_small_diff_auto_merges(self, repo: Path, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("zenagent.app.taiji.supervisor.worktrees_root", lambda: tmp_path / "home")
        chat = FakeChat()
        supervisor = make_chat_supervisor(repo, tmp_path, StubRunner(), mode="zen", chat=chat)

        await supervisor.spawn("small change", name="small")
        await supervisor.wait_idle()

        assert supervisor.list_workers()[0].status == "merged"
        assert len(chat.messages) == 1

    async def test_zen_large_diff_falls_back_to_acceptance(self, repo: Path, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("zenagent.app.taiji.supervisor.worktrees_root", lambda: tmp_path / "home")

        class BigRunner(StubRunner):
            async def run_turn(self, worktree, session_path, prompt):
                (Path(worktree) / "big.txt").write_text("x" * 300 + "\n" * 300, encoding="utf-8")
                _git(Path(worktree), "add", ".")
                _git(Path(worktree), "commit", "-qm", "big work")
                return TurnResult(status="ok", summary="wrote a lot", session_path="")

        chat = FakeChat()
        supervisor = make_chat_supervisor(repo, tmp_path, BigRunner(), mode="zen", chat=chat)

        await supervisor.spawn("big change", name="big")
        await supervisor.wait_idle()

        assert supervisor.list_workers()[0].status == "ok"
        main_before = _git(repo, "rev-parse", "main").stdout.strip()
        record = supervisor.list_workers()[0]
        assert _git(repo, "merge-base", "--is-ancestor", record.branch, main_before).returncode != 0
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
    async def test_spawn_beyond_limit_queues_until_a_slot_frees(self, repo: Path, tmp_path: Path, monkeypatch) -> None:
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

    async def test_message_wakes_even_when_slots_are_full(self, repo: Path, tmp_path: Path, monkeypatch) -> None:
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

    async def test_kill_a_queued_worker_never_runs_it(self, repo: Path, tmp_path: Path, monkeypatch) -> None:
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
    async def test_attention_is_persisted_and_relayed_and_blocks_merge(self, repo: Path, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("zenagent.app.taiji.supervisor.worktrees_root", lambda: tmp_path / "home")
        chat = FakeChat()
        question = {"request_id": "q0", "question": "方案 A 还是 B？"}
        supervisor = make_chat_supervisor(
            repo, tmp_path, AttentionRunner({"request_id": "q0", "question": "方案 A 还是 B？"}),
            mode="zen", chat=chat,
        )

        await supervisor.spawn("needs a decision", name="asking")
        await supervisor.wait_idle()

        record = supervisor.list_workers()[0]
        assert record.status == "ok"
        assert "方案 A" in record.attention
        # a pending question blocks the zen auto-merge
        main_before = _git(repo, "rev-parse", "main").stdout.strip()
        assert _git(repo, "merge-base", "--is-ancestor", record.branch, main_before).returncode != 0
        # the Router hears the question
        assert any("提问" in message and "方案 A" in message for message in chat.messages)

        # the human answers: the next turn clears the attention and merges
        supervisor.runner = StubRunner()
        await supervisor.message("asking", "答案：B")
        await supervisor.wait_idle()

        assert supervisor.list_workers()[0].attention == ""
        assert supervisor.list_workers()[0].status == "merged"

    async def test_registry_without_attention_field_loads(self, repo: Path, tmp_path: Path, monkeypatch) -> None:
        import json as _json

        monkeypatch.setattr("zenagent.app.taiji.supervisor.worktrees_root", lambda: tmp_path / "home")
        registry_path = tmp_path / "sessions" / "taiji.json"
        registry_path.parent.mkdir(parents=True)
        registry_path.write_text(_json.dumps({
            "legacy": {"name": "legacy", "branch": "task/legacy", "base": "main",
                        "worktree": "/wt", "status": "interrupted",
                        "created_at": 1.0, "updated_at": 2.0}
        }), encoding="utf-8")

        supervisor = make_supervisor(repo, tmp_path, StubRunner())
        assert supervisor.list_workers()[0].attention == ""
