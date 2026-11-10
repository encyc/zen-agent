"""SwarmManager: scheduler, role lifecycle and abort outcomes."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from conftest import text_stream

from zenagent.app.swarm.manager import SwarmManager
from zenagent.app.swarm.roles import agents_root, state_root
from zenagent.app.swarm.room import RoomMessage, SwarmStore, TaskRecord
from zenagent.events import Aborted, AssistantEvent
from zenagent.types import AssistantMessage, TextContent, Usage


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    return home


def _write_role(role_id: str, prompt: str = "You are a role.") -> Path:
    role_dir = agents_root() / role_id
    role_dir.mkdir(parents=True)
    (role_dir / "manifest.json").write_text(
        json.dumps({"id": role_id, "name": role_id.title()}), encoding="utf-8"
    )
    (role_dir / "prompt.md").write_text(prompt, encoding="utf-8")
    return role_dir


def _slow_stream(_role):
    async def stream(model, messages, tools, options):
        partial = AssistantMessage(
            content=[TextContent(text="working")],
            provider="p",
            model="m",
            usage=Usage(),
            stop_reason="pending",
            timestamp=0,
        )
        yield AssistantEvent(type="start", partial=partial)
        if options.abort is not None:
            abort = asyncio.create_task(options.abort.wait())
            sleep = asyncio.create_task(asyncio.sleep(30))
            done, pending = await asyncio.wait(
                {abort, sleep}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            if abort in done:
                raise Aborted("cancelled")
        partial.stop_reason = "stop"
        yield AssistantEvent(type="done", partial=partial, reason="stop")

    return stream


async def _wait_for(predicate, timeout: float = 15.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition was not reached in time")


def _states(manager: SwarmManager) -> dict[str, str]:
    return {task["id"]: task["state"] for task in manager.tasks()}


async def test_two_role_chain():
    _write_role("research", "You research.")
    _write_role("coder", "You code.")

    def factory(role):
        return text_stream(
            "查完了 @coder 改 Y" if role.id == "research" else "改完了 @you 验收"
        )

    manager = SwarmManager(stream_fn_factory=factory)
    await manager.start()
    try:
        tasks = await manager.post("@research 查 X")
        assert [task.assignee for task in tasks] == ["research"]
        await _wait_for(
            lambda: (
                len(manager.tasks()) == 2
                and all(task["state"] == "done" for task in manager.tasks())
            )
        )
        by_assignee = {task["assignee"]: task["state"] for task in manager.tasks()}
        assert by_assignee == {"research": "done", "coder": "done"}
        texts = [message["text"] for message in manager.messages()]
        assert "查完了 @coder 改 Y" in texts
        assert "改完了 @you 验收" in texts
    finally:
        await manager.close()


async def test_cancel_running_task():
    _write_role("slow")
    manager = SwarmManager(stream_fn_factory=_slow_stream)
    await manager.start()
    try:
        [task] = await manager.post("@slow 慢慢做")
        await _wait_for(lambda: _states(manager)[task.id] == "running")
        assert await manager.cancel(task.id) is True
        await _wait_for(lambda: _states(manager)[task.id] == "cancelled")
    finally:
        await manager.close()


async def test_retry_creates_derived_task():
    _write_role("worker")
    store = SwarmStore(state_root())
    store.load()
    store.append_message(
        RoomMessage(
            id="m_fail",
            ts=1,
            author_kind="user",
            author_id="you",
            text="@worker do it",
            mentions=["worker"],
        )
    )
    store.append_task(
        TaskRecord(
            id="t_fail",
            assignee="worker",
            state="queued",
            source_message_id="m_fail",
            chain_id="c_1",
            chain_depth=1,
            created_at=1,
            updated_at=1,
        )
    )
    store.append_task(
        TaskRecord(
            id="t_fail",
            assignee="worker",
            state="failed",
            source_message_id="m_fail",
            chain_id="c_1",
            chain_depth=1,
            created_at=1,
            updated_at=2,
            reason="error",
        )
    )

    manager = SwarmManager(stream_fn_factory=lambda role: text_stream("done"))
    await manager.start()
    try:
        retried = await manager.retry("t_fail")
        assert retried is not None
        assert retried.derived_from == "t_fail"
        assert retried.source_message_id == "m_fail"
        assert retried.state == "queued"
        await _wait_for(lambda: _states(manager).get(retried.id) == "done")
    finally:
        await manager.close()


async def test_restart_marks_nonterminal_interrupted():
    _write_role("worker")
    store = SwarmStore(state_root())
    store.load()
    store.append_message(
        RoomMessage(
            id="m_seed",
            ts=1,
            author_kind="user",
            author_id="you",
            text="@worker go",
            mentions=["worker"],
        )
    )
    store.append_task(
        TaskRecord(
            id="t_queued",
            assignee="worker",
            state="queued",
            source_message_id="m_seed",
            chain_id="c_1",
            chain_depth=1,
            created_at=1,
            updated_at=1,
        )
    )
    store.append_task(
        TaskRecord(
            id="t_running",
            assignee="worker",
            state="queued",
            source_message_id="m_seed",
            chain_id="c_2",
            chain_depth=1,
            created_at=1,
            updated_at=1,
        )
    )
    store.append_task(
        TaskRecord(
            id="t_running",
            assignee="worker",
            state="running",
            source_message_id="m_seed",
            chain_id="c_2",
            chain_depth=1,
            created_at=1,
            updated_at=2,
        )
    )

    manager = SwarmManager(stream_fn_factory=lambda role: text_stream("done"))
    await manager.start()
    try:
        tasks = {task["id"]: task for task in manager.tasks()}
        for task_id in ("t_queued", "t_running"):
            assert tasks[task_id]["state"] == "failed"
            assert tasks[task_id]["reason"] == "interrupted"
        assert any("interrupted" in message["text"] for message in manager.messages())
    finally:
        await manager.close()


async def test_role_update_applies_after_run():
    _write_role("slow", "You are v1.")
    manager = SwarmManager(stream_fn_factory=_slow_stream)
    await manager.start()
    try:
        [task] = await manager.post("@slow work")
        await _wait_for(lambda: _states(manager)[task.id] == "running")
        await manager.update_role(
            "slow",
            {"id": "slow", "name": "Slow v2", "systemPrompt": "You are v2."},
        )
        pending = manager.agents["slow"].pending_apply
        assert pending is not None
        assert pending.name == "Slow v2"
        assert "You are v1." in manager.agents["slow"].agent.system_prompt

        assert await manager.cancel(task.id) is True

        def applied() -> bool:
            agent = manager.agents.get("slow")
            return agent is not None and agent.pending_apply is None

        await _wait_for(applied)
        assert manager.agents["slow"].role.name == "Slow v2"
        assert "You are v2." in manager.agents["slow"].agent.system_prompt
    finally:
        await manager.close()


async def test_delete_role_cancels_tasks():
    _write_role("victim")
    manager = SwarmManager(stream_fn_factory=_slow_stream)
    await manager.start()
    try:
        [running_task] = await manager.post("@victim one")
        await _wait_for(lambda: _states(manager)[running_task.id] == "running")
        [queued_task] = await manager.post("@victim two")

        await manager.delete_role("victim")

        tasks = {task["id"]: task for task in manager.tasks()}
        for task_id in (running_task.id, queued_task.id):
            assert tasks[task_id]["state"] == "cancelled"
            assert tasks[task_id]["reason"] == "role_removed"
        assert "victim" not in manager.agents
        assert not (agents_root() / "victim").exists()
    finally:
        await manager.close()
