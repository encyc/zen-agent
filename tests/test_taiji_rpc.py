"""The ``taiji`` RPC kind: read-only panel snapshots."""

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


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
async def test_snapshot_returns_graph_and_sessions(
    runtime_factory, repo: Path, monkeypatch
) -> None:
    monkeypatch.chdir(repo)
    server = build_server(await runtime_factory(), writes := [])

    result = await call(server, writes, {"type": "taiji", "op": "snapshot"})

    assert result["ok"] is True
    graph = result["taiji"]["graph"]
    assert graph["is_repo"] is True
    assert [c["subject"] for c in graph["commits"]] == ["init"]
    sessions = result["taiji"]["sessions"]
    assert sessions["repo_root"] == str(repo.resolve())
    assert sessions["projects"][0]["branch"] == "main"


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
async def test_unknown_op_is_an_error(runtime_factory, repo: Path, monkeypatch) -> None:
    monkeypatch.chdir(repo)
    server = build_server(await runtime_factory(), writes := [])

    result = await call(server, writes, {"type": "taiji", "op": "ghost"})

    assert result["type"] == "error"
    assert "ghost" in result["message"]


async def test_workers_op_reports_the_registry(
    runtime_factory, repo: Path, monkeypatch, tmp_path: Path
) -> None:
    from zenagent.app.taiji.registry import TaijiRegistry, WorkerRecord
    from zenagent.project import project_dir

    monkeypatch.chdir(repo)
    registry_dir = project_dir(root=repo)
    registry_dir.mkdir(parents=True, exist_ok=True)
    registry = TaijiRegistry()
    registry.records["w"] = WorkerRecord(
        name="w",
        branch="task/w",
        base="main",
        worktree="/wt",
        status="ok",
        created_at=1.0,
        updated_at=2.0,
        summary="did it",
        attention='{"request_id": "q0", "question": "A 还是 B？"}',
    )
    registry.save(registry_dir / "taiji.json")

    server = build_server(await runtime_factory(), writes := [])
    result = await call(server, writes, {"type": "taiji", "op": "workers"})

    assert result["ok"] is True
    row = result["workers"]["w"]
    assert row["status"] == "ok"
    assert row["summary"] == "did it"
    assert "A 还是 B" in row["attention"]
