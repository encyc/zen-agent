"""Idle (role, work item) runtimes are reclaimed; live work pins them."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from conftest import text_stream

from zenagent.app.swarm.manager import SwarmManager
from zenagent.app.swarm.roles import project_agents_root
from zenagent.app.swarm.room import TaskRecord


def _write_role(root: Path, role_id: str) -> None:
    directory = project_agents_root(root) / role_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "manifest.json").write_text(
        json.dumps({"id": role_id, "name": role_id.title()}), encoding="utf-8"
    )


async def _wait_for(predicate, timeout: float = 5.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition was not reached in time")


@pytest.fixture
def project_root(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    root = tmp_path / "project"
    root.mkdir()
    _write_role(root, "research")
    return root


def _manager(root: Path, *, ttl: float) -> SwarmManager:
    return SwarmManager(
        root=root,
        stream_fn_factory=lambda _role: text_stream("done"),
        runtime_idle_s=ttl,
    )


async def test_idle_run_unit_is_reclaimed_after_ttl(project_root):
    manager = _manager(project_root, ttl=0.12)
    await manager.start()
    try:
        [task] = await manager.post("@research 查一下")
        await _wait_for(
            lambda: manager.store.tasks[task.id].state == "done",
        )
        assert manager.agents, "the run unit should exist after its round"
        await _wait_for(lambda: not manager.agents, timeout=3.0)
        assert manager.store.tasks[task.id].state == "done"
    finally:
        await manager.close()


async def test_zero_ttl_disables_reclamation(project_root):
    manager = _manager(project_root, ttl=0)
    await manager.start()
    try:
        [task] = await manager.post("@research 查一下")
        await _wait_for(
            lambda: manager.store.tasks[task.id].state == "done",
        )
        await asyncio.sleep(0.25)
        assert manager.agents, "ttl=0 must keep the instance resident"
    finally:
        await manager.close()


async def test_pending_round_pins_the_instance(project_root):
    manager = _manager(project_root, ttl=0.01)
    await manager.start()
    try:
        role = manager.role("research")
        key = (role.id, None)
        agent = await manager._instance(role, None)
        assert agent is not None
        # Pretend the instance has been idle forever, then queue a round for
        # it: the queued round must win over reclamation.
        manager._idle.touch(key, now=0.0)
        manager._pending.append(
            TaskRecord(id="t_pending", assignee=role.id, work_item_id=None)
        )
        await manager._reap_idle()
        assert key in manager.agents
        manager._pending.clear()
    finally:
        await manager.close()
