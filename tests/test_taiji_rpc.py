"""The ``taiji`` RPC kind: the dispatch board read op and the four actions."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from zenagent.cli.rpc import RpcServer


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True
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


async def call(server: RpcServer, writes: list[str], payload: dict) -> dict:
    payload.setdefault("id", "t1")
    assert await server._handle_line(json.dumps(payload))
    return json.loads(writes[-1])


def build_server(runtime, writes: list[str]) -> RpcServer:
    return RpcServer(runtime=runtime, write_line=writes.append)


class StubRunner:
    """Turns that commit one file into the worktree and finish immediately."""

    def __init__(self) -> None:
        from zenagent.app.taiji.supervisor import TurnResult

        self.TurnResult = TurnResult

    async def run_turn(self, worktree: str, session_path, prompt: str):
        result_path = Path(worktree) / "work.txt"
        result_path.write_text(prompt, encoding="utf-8")
        _git(Path(worktree), "add", "-A")
        _git(Path(worktree), "commit", "-qm", f"work: {prompt[:20]}")
        return self.TurnResult(
            status="ok", summary="did it", session_path=str(result_path)
        )


def bind_supervisor(
    repo: Path, tmp_path: Path, monkeypatch, *, runner=None
):
    """Construct the process singleton the way the host's serve does."""
    from zenagent import project
    from zenagent.app.taiji import supervisor as supervisor_module
    from zenagent.app.taiji.supervisor import TaijiSupervisor

    monkeypatch.setattr(
        "zenagent.app.taiji.supervisor.worktrees_root", lambda: tmp_path / "home"
    )
    supervisor = TaijiSupervisor(
        repo_root=repo,
        sessions_dir=project.project_sessions_dir(root=repo),
        registry_path=project.project_dir(root=repo) / "taiji.json",
        runner=runner or StubRunner(),
        chat=None,
        mode_of=lambda: "ask",
    )
    supervisor_module._SUPERVISORS[repo.resolve()] = supervisor
    return supervisor


@pytest.fixture(autouse=True)
def clean_singleton():
    from zenagent.app.taiji import supervisor as supervisor_module

    supervisor_module._SUPERVISORS.clear()
    yield
    supervisor_module._SUPERVISORS.clear()


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
async def test_board_without_a_supervisor_reports_inactive(
    runtime_factory, repo: Path, monkeypatch
) -> None:
    monkeypatch.chdir(repo)
    server = build_server(await runtime_factory(), writes := [])

    result = await call(server, writes, {"type": "taiji", "op": "board"})

    assert result["ok"] is True
    board = result["board"]
    assert board == {
        "active": False,
        "router_busy": False,
        "slots": None,
        "workers": [],
    }


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
async def test_board_reports_workers_diffstat_slots_and_attention(
    runtime_factory, repo: Path, monkeypatch, tmp_path: Path
) -> None:
    supervisor = bind_supervisor(repo, tmp_path, monkeypatch)
    await supervisor.spawn("task one", name="w")
    await supervisor.wait_idle()
    supervisor._record("w").attention = json.dumps(
        {"request_id": "q0", "question": "A 还是 B？", "options": ["A", "B"]},
        ensure_ascii=False,
    )
    supervisor._save()

    monkeypatch.chdir(repo)
    server = build_server(await runtime_factory(), writes := [])
    result = await call(server, writes, {"type": "taiji", "op": "board"})

    assert result["ok"] is True
    board = result["board"]
    assert board["active"] is True
    assert board["slots"] == {"busy": 0, "max": 3, "queued": 0}
    worker = board["workers"][0]
    assert worker["name"] == "w"
    assert worker["status"] == "ok"
    assert worker["branch"] == "task/w"
    assert worker["summary"] == "did it"
    assert worker["attention"]["question"] == "A 还是 B？"
    assert worker["attention"]["options"] == ["A", "B"]
    assert worker["diffstat"] is not None
    assert any(entry["path"] == "work.txt" for entry in worker["diffstat"])


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
async def test_board_diffstat_is_null_off_acceptance(
    runtime_factory, repo: Path, monkeypatch, tmp_path: Path
) -> None:
    supervisor = bind_supervisor(repo, tmp_path, monkeypatch)
    await supervisor.spawn("task one", name="w")
    await supervisor.wait_idle()

    monkeypatch.chdir(repo)
    server = build_server(await runtime_factory(), writes := [])
    result = await call(server, writes, {"type": "taiji", "op": "board"})

    worker = result["board"]["workers"][0]
    assert worker["status"] == "ok"
    assert worker["diffstat"] is not None

    supervisor._record("w").status = "running"
    result = await call(server, writes, {"type": "taiji", "op": "board"})
    assert result["board"]["workers"][0]["diffstat"] is None


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
async def test_message_op_runs_a_turn_or_reports_queued(
    runtime_factory, repo: Path, monkeypatch, tmp_path: Path
) -> None:
    from tests.test_taiji_supervisor import GatedRunner

    runner = GatedRunner()
    supervisor = bind_supervisor(repo, tmp_path, monkeypatch, runner=runner)
    await supervisor.spawn("task one", name="w")
    await asyncio.sleep(0.05)  # w now holds a gated turn

    monkeypatch.chdir(repo)
    server = build_server(await runtime_factory(), writes := [])

    result = await call(
        server, writes, {"type": "taiji", "op": "message", "name": "w", "text": "插话"}
    )
    assert result["ok"] is True
    assert result["queued"] is True

    runner.gate("w").set()
    await supervisor.wait_idle()

    result = await call(
        server, writes, {"type": "taiji", "op": "message", "name": "w", "text": "再来"}
    )
    assert result["ok"] is True
    assert result["queued"] is False
    await supervisor.wait_idle()


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
async def test_message_to_an_unknown_worker_is_an_error(
    runtime_factory, repo: Path, monkeypatch, tmp_path: Path
) -> None:
    bind_supervisor(repo, tmp_path, monkeypatch)
    monkeypatch.chdir(repo)
    server = build_server(await runtime_factory(), writes := [])

    result = await call(
        server, writes, {"type": "taiji", "op": "message", "name": "ghost", "text": "?"}
    )

    assert result["type"] == "error"


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
async def test_merge_failure_carries_the_block_reason(
    runtime_factory, repo: Path, monkeypatch, tmp_path: Path
) -> None:
    from zenagent.app.git import reset_cache

    supervisor = bind_supervisor(repo, tmp_path, monkeypatch)
    await supervisor.spawn("task one", name="w")
    await supervisor.wait_idle()
    (repo / "dirty.txt").write_text("wip", encoding="utf-8")
    reset_cache()

    monkeypatch.chdir(repo)
    server = build_server(await runtime_factory(), writes := [])
    result = await call(server, writes, {"type": "taiji", "op": "merge", "name": "w"})

    assert result["type"] == "error"
    assert "主工作区" in result["message"]
    await supervisor.wait_idle()


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
async def test_kill_op_terminates_a_worker(
    runtime_factory, repo: Path, monkeypatch, tmp_path: Path
) -> None:
    supervisor = bind_supervisor(repo, tmp_path, monkeypatch)
    await supervisor.spawn("task one", name="w")
    await supervisor.wait_idle()

    monkeypatch.chdir(repo)
    server = build_server(await runtime_factory(), writes := [])
    result = await call(server, writes, {"type": "taiji", "op": "kill", "name": "w"})

    assert result["ok"] is True
    assert supervisor._record("w").status == "killed"


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
async def test_peek_op_reads_the_worker_transcript_tail(
    runtime_factory, repo: Path, monkeypatch, tmp_path: Path
) -> None:
    supervisor = bind_supervisor(repo, tmp_path, monkeypatch)
    await supervisor.spawn("task one", name="w")
    await supervisor.wait_idle()
    transcript = tmp_path / "w.jsonl"
    transcript.write_text(
        json.dumps({"role": "user", "content": "fix it"}, ensure_ascii=False)
        + "\n"
        + json.dumps(
            {"role": "assistant", "content": [{"type": "text", "text": "已修"}]},
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    supervisor._record("w").session = str(transcript)

    monkeypatch.chdir(repo)
    server = build_server(await runtime_factory(), writes := [])
    result = await call(server, writes, {"type": "taiji", "op": "peek", "name": "w"})

    assert result["ok"] is True
    assert result["items"] == [
        {"role": "user", "text": "fix it"},
        {"role": "assistant", "text": "已修"},
    ]


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
async def test_peek_on_a_worker_without_a_session_is_empty(
    runtime_factory, repo: Path, monkeypatch, tmp_path: Path
) -> None:
    bind_supervisor(repo, tmp_path, monkeypatch)
    monkeypatch.chdir(repo)
    server = build_server(await runtime_factory(), writes := [])
    result = await call(server, writes, {"type": "taiji", "op": "peek", "name": "ghost"})

    assert result["ok"] is True
    assert result["items"] == []


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
async def test_unknown_op_is_an_error(runtime_factory, repo: Path, monkeypatch) -> None:
    monkeypatch.chdir(repo)
    server = build_server(await runtime_factory(), writes := [])

    result = await call(server, writes, {"type": "taiji", "op": "snapshot"})

    assert result["type"] == "error"
    assert "snapshot" in result["message"]


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
async def test_kill_unknown_worker_is_an_error_frame_not_a_crash(
    runtime_factory, repo: Path, monkeypatch, tmp_path: Path
) -> None:
    bind_supervisor(repo, tmp_path, monkeypatch)
    monkeypatch.chdir(repo)
    server = build_server(await runtime_factory(), writes := [])

    result = await call(server, writes, {"type": "taiji", "op": "kill", "name": "ghost"})

    assert result["type"] == "error"
    assert "ghost" in result["message"]
    # the read pump survives: a follow-up request still answers
    follow = await call(server, writes, {"type": "taiji", "op": "board"})
    assert follow["ok"] is True


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
async def test_peek_with_a_bad_limit_is_an_error_frame_not_a_crash(
    runtime_factory, repo: Path, monkeypatch, tmp_path: Path
) -> None:
    bind_supervisor(repo, tmp_path, monkeypatch)
    monkeypatch.chdir(repo)
    server = build_server(await runtime_factory(), writes := [])

    result = await call(server, writes, {"type": "taiji", "op": "peek", "name": "w", "limit": "abc"})

    assert result["type"] == "error"
