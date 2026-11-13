"""The three orchestration scopes write distinguishable ledger rows."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from conftest import text_stream

from zenagent.app.agent_profile import Supervisor
from zenagent.app.runs import RunLedger
from zenagent.app.swarm.manager import SwarmManager
from zenagent.app.swarm.roles import project_agents_root
from zenagent.app.zen.fleet import WorkerHandle, ZenFleet
from zenagent.project import project_dir


def _write_role(root: Path, role_id: str) -> None:
    directory = project_agents_root(root) / role_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "manifest.json").write_text(
        json.dumps({"id": role_id, "name": role_id.title()}), encoding="utf-8"
    )


async def _wait_for(predicate, timeout: float = 10.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition was not reached in time")


async def test_zen_worker_scope_names_the_worker(tmp_path, monkeypatch):
    from zenagent.app.zen import fleet as fleet_module

    class FakeRuntime:
        def __init__(self):
            self.run_scope = {}
            self.session = SimpleNamespace(path=tmp_path / "worker.jsonl")

    class FakeBroker:
        def set_confirm(self, _confirm):
            return None

    class FakePrepared:
        def __init__(self):
            self.runtime = FakeRuntime()
            self.broker = FakeBroker()

    async def fake_prepare(_options, **_kwargs):
        return FakePrepared()

    monkeypatch.setattr(fleet_module, "prepare", fake_prepare)
    worker_path = tmp_path / "worker.jsonl"
    worker_path.write_text("", encoding="utf-8")
    supervisor = Supervisor(tmp_path)
    fleet = ZenFleet(
        chat=None,
        root=tmp_path,
        sessions_dir=tmp_path / "sessions",
        router_path=str(tmp_path / "router.jsonl"),
        supervisor=supervisor,
    )
    handle = WorkerHandle(str(worker_path), "worker-a", "tests")
    prepared = await fleet._build_worker(handle)

    assert prepared.runtime.run_scope["kind"] == "zen_worker"
    assert prepared.runtime.run_scope["agentId"] == "worker-a"
    assert prepared.runtime.run_scope["label"] == "tests"
    assert prepared.runtime.run_scope["parentRunId"] is None
    assert [handle.key for handle in supervisor.handles()] == ["zen:worker"]


async def test_swarm_task_writes_a_swarm_run_record(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    root = tmp_path / "project"
    root.mkdir()
    _write_role(root, "research")

    manager = SwarmManager(
        root=root, stream_fn_factory=lambda _role: text_stream("done")
    )
    await manager.start()
    try:
        [task] = await manager.post("@research 查一下")
        await _wait_for(
            lambda: manager.store.tasks[task.id].state == "done",
        )
        rows = RunLedger(project_dir(root=root) / "runs.jsonl").list(kind="swarm")
        assert len(rows) == 1
        row = rows[0]
        assert row.status == "done"
        assert row.termination == "stop"
        assert row.agent_id == "research"
        assert row.work_item_id
        assert row.task_id == task.id
        assert row.session
        assert [handle.key for handle in manager.supervisor.handles()] == [
            f"research@{row.work_item_id}"
        ]
    finally:
        await manager.close()


async def test_zen_router_scope_follows_attach_and_detach(tmp_path):
    from zenagent.cli.rpc.ops_zen import ZenOps

    class FakeRuntime:
        def __init__(self):
            self.root = tmp_path
            self.session = SimpleNamespace(path=tmp_path / "router.jsonl")
            self.zen_fleet = None
            self.user_config = None

        def _recompose(self):
            return None

    runtime = FakeRuntime()
    slot = SimpleNamespace(
        runtime=runtime,
        chat=None,
        server=SimpleNamespace(confirm=None, ask=None),
    )
    ops = ZenOps()
    ops._attach_fleet(tmp_path, slot)
    assert runtime.run_scope["kind"] == "zen_router"
    assert runtime.run_scope["agentId"] == "router"
    await ops._detach_fleet(slot)
    assert runtime.run_scope["kind"] == "coding"
    assert runtime.run_scope["agentId"] == "main"
