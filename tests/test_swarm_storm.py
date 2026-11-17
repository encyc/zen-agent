"""The reported failure: one agent asks the others to say hello, and every
hello becomes a new hello.

This is the end-to-end guard. The fake model is deliberately the worst case:
whatever role it is, its final text greets *every* other role. Before the
message budget existed that produced a depth-8 tree of O(k^8) rounds; the test
asserts the room now stops at the chain budget, without any help from the
prompt.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import text_stream

from zenagent.app.swarm.manager import SwarmManager
from zenagent.app.swarm.roles import project_agents_root

_PROJECT_ROOT: Path | None = None

ROLES = ("alpha", "beta", "gamma", "delta")


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    project_root = tmp_path / "project"
    project_root.mkdir()
    global _PROJECT_ROOT
    _PROJECT_ROOT = project_root
    yield home
    _PROJECT_ROOT = None


def _project_root() -> Path:
    assert _PROJECT_ROOT is not None
    return _PROJECT_ROOT


def _manager(**kwargs) -> SwarmManager:
    return SwarmManager(root=_project_root(), **kwargs)


def _write_role(role_id: str) -> None:
    role_dir = project_agents_root(_project_root()) / role_id
    role_dir.mkdir(parents=True)
    (role_dir / "manifest.json").write_text(
        json.dumps({"id": role_id, "name": role_id.title()}), encoding="utf-8"
    )
    (role_dir / "prompt.md").write_text("You are a role.", encoding="utf-8")


async def _wait_for(predicate, timeout: float = 30.0) -> None:
    import asyncio

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition was not reached in time")


def _greets_everyone(role):
    others = [name for name in ROLES if name != role.id]
    return text_stream("hi " + " ".join(f"@{name}" for name in others))


async def test_greeting_storm_stops_at_the_chain_budget():
    for name in ROLES:
        _write_role(name)
    manager = _manager(stream_fn_factory=_greets_everyone)
    await manager.start()
    try:
        await manager.post("@alpha 叫大家出来打个招呼")
        await _wait_for(
            lambda: (
                not any(
                    task["state"] in ("queued", "running") for task in manager.tasks()
                )
            )
        )
        tasks = manager.tasks()
        assert tasks, "the first delegation should still happen"
        assert len(tasks) <= manager.router.gate.limits.max_chain_tasks
        notes = [
            message["text"]
            for message in manager.messages()
            if message["author"]["kind"] == "system"
        ]
        assert any(
            reason in text
            for text in notes
            for reason in ("chain_tasks", "chain_burst", "edge_repeat", "reply_cap")
        ), notes
    finally:
        await manager.close()


async def test_a_result_may_hand_work_to_one_role_only():
    for name in ("alpha", "beta", "gamma"):
        _write_role(name)

    def factory(role):
        others = [name for name in ("alpha", "beta", "gamma") if name != role.id]
        return text_stream(" ".join(f"@{name}" for name in others) + " 继续")

    manager = _manager(stream_fn_factory=factory)
    await manager.start()
    try:
        await manager.post("@alpha 开始")
        await _wait_for(
            lambda: (
                not any(
                    task["state"] in ("queued", "running") for task in manager.tasks()
                )
            )
        )
        rows = manager.tasks()
        alpha = next(task for task in rows if task["assignee"] == "alpha")
        # alpha's result is a reply: of its two mentions, exactly one is work.
        first_hop = [
            task for task in rows if task["sourceMessageId"] == alpha["resultMessageId"]
        ]
        assert [task["assignee"] for task in first_hop] == ["beta"]
        notes = [
            message["text"]
            for message in manager.messages()
            if message["author"]["kind"] == "system"
        ]
        assert any("reply_cap" in text for text in notes), notes
    finally:
        await manager.close()
