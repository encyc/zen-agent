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
from zenagent.types import (
    AssistantMessage,
    TextContent,
    ToolCall,
    ToolResultMessage,
    Usage,
)


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


async def test_unbuildable_role_is_skipped_with_a_room_note():
    _write_role("research", "You research.")
    broken = agents_root() / "broken"
    broken.mkdir(parents=True)
    (broken / "manifest.json").write_text(
        json.dumps({"id": "broken", "name": "Broken", "model": "no-such-model"}),
        encoding="utf-8",
    )
    (broken / "prompt.md").write_text("You break.", encoding="utf-8")

    manager = SwarmManager(stream_fn_factory=lambda role: text_stream("ok"))
    await manager.start()
    try:
        assert "research" in manager.agents
        assert "broken" not in manager.agents
        [task] = await manager.post("@research hi")
        await _wait_for(lambda: _states(manager).get(task.id) == "done")
        notes = [
            message["text"]
            for message in manager.messages()
            if message["author"]["kind"] == "system"
        ]
        assert any("broken" in text for text in notes)
    finally:
        await manager.close()


async def test_reload_clears_a_fixed_role_warning():
    _write_role("research", "You research.")
    broken = agents_root() / "broken"
    broken.mkdir(parents=True)
    (broken / "manifest.json").write_text(
        json.dumps({"id": "broken", "name": "Broken", "model": "no-such-model"}),
        encoding="utf-8",
    )
    (broken / "prompt.md").write_text("You break.", encoding="utf-8")

    manager = SwarmManager(stream_fn_factory=lambda role: text_stream("ok"))
    await manager.start()
    try:
        assert any(warning.startswith("role broken:") for warning in manager.warnings)
        (broken / "manifest.json").write_text(
            json.dumps({"id": "broken", "name": "Broken"}), encoding="utf-8"
        )
        await manager.reload()
        assert "broken" in manager.agents
        assert not any(
            warning.startswith("role broken:") for warning in manager.warnings
        )
    finally:
        await manager.close()


async def test_close_is_safe_after_start_failure():
    _write_role("research", "You research.")
    broken = agents_root() / "broken"
    broken.mkdir(parents=True)
    (broken / "manifest.json").write_text(
        json.dumps({"id": "broken", "name": "Broken", "model": "no-such-model"}),
        encoding="utf-8",
    )
    (broken / "prompt.md").write_text("You break.", encoding="utf-8")

    manager = SwarmManager(stream_fn_factory=lambda role: text_stream("ok"))
    await manager.start()
    await manager.close()
    await manager.close()
    assert manager.agents == {}


async def test_agent_mentioning_you_raises_a_notification():
    _write_role("research", "You research.")
    frames: list[dict] = []
    manager = SwarmManager(
        write_frame=frames.append,
        stream_fn_factory=lambda role: text_stream("@you 结论是 Z"),
    )
    await manager.start()
    try:
        [task] = await manager.post("@research 查 X")
        await _wait_for(lambda: _states(manager).get(task.id) == "done")
        notices = [frame for frame in frames if frame.get("type") == "swarm_notice"]
        assert len(notices) == 1
        assert notices[0]["text"] == "@research: @you 结论是 Z"
        assert "swarm" not in notices[0]
    finally:
        await manager.close()


async def test_user_mentioning_you_does_not_notify():
    _write_role("research", "You research.")
    frames: list[dict] = []
    manager = SwarmManager(
        write_frame=frames.append,
        stream_fn_factory=lambda role: text_stream("ok"),
    )
    await manager.start()
    try:
        await manager.post("@you 提醒我自己")
        assert not [frame for frame in frames if frame.get("type") == "swarm_notice"]
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


def _write_then_text_stream(path: str = "note.txt"):
    async def stream(model, messages, tools, options):
        partial = AssistantMessage(
            content=[],
            provider="p",
            model="m",
            usage=Usage(),
            stop_reason="pending",
            timestamp=0,
        )
        yield AssistantEvent(type="start", partial=partial)
        if isinstance(messages[-1], ToolResultMessage):
            partial.content = [TextContent(text="wrote")]
            partial.stop_reason = "stop"
            yield AssistantEvent(type="done", partial=partial, reason="stop")
            return
        call = ToolCall(id="c1", name="write_file", arguments={"path": path, "content": "hi"})
        partial.content = [call]
        yield AssistantEvent(
            type="toolcall_start", partial=partial, content_index=0, tool_call=call
        )
        yield AssistantEvent(
            type="toolcall_end", partial=partial, content_index=0, tool_call=call
        )
        partial.stop_reason = "tool_use"
        yield AssistantEvent(type="done", partial=partial, reason="tool_use")

    return stream


def _approval_request(frames: list[dict]) -> dict | None:
    for frame in frames:
        event = frame.get("event")
        if (
            frame.get("type") == "event"
            and isinstance(event, dict)
            and event.get("type") == "approval_request"
        ):
            return event
    return None


async def test_unknown_mention_lands_in_the_room_without_a_task():
    _write_role("research")
    frames: list[dict] = []
    manager = SwarmManager(
        write_frame=frames.append, stream_fn_factory=lambda role: text_stream("ok")
    )
    await manager.start()
    try:
        accepted = await manager.post("@ghost 在吗")
        assert accepted == []
        assert manager.tasks() == []
        assert manager.messages()[-1]["text"] == "@ghost 在吗"
        assert not [frame for frame in frames if frame.get("type") == "swarm_task"]
    finally:
        await manager.close()


async def test_mutual_mentions_stop_at_the_depth_cap():
    _write_role("ping")
    _write_role("pong")
    manager = SwarmManager(
        stream_fn_factory=lambda role: text_stream(
            "@pong again" if role.id == "ping" else "@ping again"
        )
    )
    await manager.start()
    try:
        await manager.post("@ping go")
        await _wait_for(
            lambda: any(
                "chain_depth" in message["text"]
                for message in manager.messages()
                if message["author"]["kind"] == "system"
            )
        )
        await _wait_for(
            lambda: not any(
                task["state"] in ("queued", "running") for task in manager.tasks()
            )
        )
        tasks = manager.tasks()
        assert max(task["chainDepth"] for task in tasks) == 8
        notes = [
            message["text"]
            for message in manager.messages()
            if message["author"]["kind"] == "system"
        ]
        assert any("chain_depth" in text for text in notes)
    finally:
        await manager.close()


async def test_approval_allow_lets_the_write_run():
    role_dir = _write_role("writer")
    (role_dir / "manifest.json").write_text(
        json.dumps(
            {"id": "writer", "name": "Writer", "tools": ["write_file"]}
        ),
        encoding="utf-8",
    )
    frames: list[dict] = []
    manager = SwarmManager(
        write_frame=frames.append,
        stream_fn_factory=lambda role: _write_then_text_stream(),
    )
    await manager.start()
    try:
        [task] = await manager.post("@writer 写个文件")
        await _wait_for(lambda: _approval_request(frames) is not None)
        request = _approval_request(frames)
        assert request is not None
        assert request["swarm"] is True
        assert request["request_id"].startswith("swarm:")
        assert manager.resolve_approval(request["request_id"], "allow") is True
        await _wait_for(lambda: _states(manager)[task.id] == "done")
        root = manager.agents["writer"].role.root
        assert (root / "note.txt").read_text(encoding="utf-8") == "hi"
    finally:
        await manager.close()


async def test_approval_timeout_fails_the_task(monkeypatch):
    import zenagent.app.swarm.manager as manager_module

    monkeypatch.setattr(manager_module, "APPROVAL_TIMEOUT_S", 0.05)
    role_dir = _write_role("writer")
    (role_dir / "manifest.json").write_text(
        json.dumps(
            {"id": "writer", "name": "Writer", "tools": ["write_file"]}
        ),
        encoding="utf-8",
    )
    manager = SwarmManager(
        stream_fn_factory=lambda role: _write_then_text_stream()
    )
    await manager.start()
    try:
        [task] = await manager.post("@writer 写个文件")
        await _wait_for(lambda: _states(manager)[task.id] == "failed")
        [record] = [item for item in manager.tasks() if item["id"] == task.id]
        assert record["reason"] == "approval_timeout"
    finally:
        await manager.close()


async def test_inbox_cap_rejects_the_overflow_posts():
    _write_role("slow")
    manager = SwarmManager(stream_fn_factory=_slow_stream)
    await manager.start()
    try:
        [running] = await manager.post("@slow one")
        await _wait_for(lambda: _states(manager)[running.id] == "running")
        accepted = 0
        for index in range(17):
            accepted += len(await manager.post(f"@slow more {index}"))
        assert accepted == 16
        notes = [
            message["text"]
            for message in manager.messages()
            if message["author"]["kind"] == "system"
        ]
        assert any("inbox_full" in text for text in notes)
    finally:
        await manager.close()


async def test_built_role_sessions_do_not_report_the_unscoped_warning():
    _write_role("research", "You research.")
    manager = SwarmManager(stream_fn_factory=lambda role: text_stream("ok"))
    await manager.start()
    try:
        assert "research" in manager.agents
        # Role sessions live under the swarm state dir by design; the runtime
        # must not mistake that for a legacy project transcript.
        assert not any("legacy (unscoped)" in warning for warning in manager.warnings)
    finally:
        await manager.close()
