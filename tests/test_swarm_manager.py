"""SwarmManager: scheduler, role lifecycle and abort outcomes."""

from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import replace
from pathlib import Path

import pytest
from conftest import text_stream

from zenagent.app.runtime import Runtime
from zenagent.app.swarm.events_tap import subscribe_events, tap
from zenagent.app.swarm.manager import SwarmManager
from zenagent.app.swarm.roles import (
    project_agents_root,
    project_state_root,
    state_root,
)
from zenagent.app.swarm.room import RoomMessage, SwarmStore, TaskRecord
from zenagent.events import Aborted, AgentEvent, AssistantEvent
from zenagent.project import project_key
from zenagent.types import (
    AssistantMessage,
    TextContent,
    ToolCall,
    ToolResultMessage,
    Usage,
)

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

_PROJECT_ROOT: Path | None = None


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


def _role_dir(role_id: str) -> Path:
    return project_agents_root(_project_root()) / role_id


def _write_role(role_id: str, prompt: str = "You are a role.") -> Path:
    role_dir = _role_dir(role_id)
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


async def test_close_fails_pending_approvals():
    _write_role("research", "You research.")
    manager = _manager(stream_fn_factory=lambda role: text_stream("ok"))
    await manager.start()
    try:
        confirm = manager._confirm_callback("research")
        # A tool awaiting confirm, modeled as a run blocked on the approval
        # future exactly as _confirm_callback registers it.
        run = asyncio.create_task(confirm("write_file", {"path": "x"}))
        manager._running[("research", None)] = run
        await _wait_for(lambda: len(manager._approvals) == 1)
        # close() must fail the pending approval promptly; otherwise the gather
        # of running tasks stalls for APPROVAL_TIMEOUT_S (120 s).
        await asyncio.wait_for(manager.close(), timeout=5)
        assert await asyncio.wait_for(run, timeout=5) == "deny"
    finally:
        await manager.close()


async def test_configured_policy_reaches_the_router():
    manager = _manager(max_concurrent=5, max_chain_depth=2, max_chain_cost=1.25)
    await manager.start()
    try:
        assert manager.router is not None
        assert manager.router.max_chain_depth == 2
        assert manager.router.max_chain_cost == 1.25
        assert manager._sem._value == 5
    finally:
        await manager.close()


async def test_two_role_chain():
    _write_role("research", "You research.")
    _write_role("coder", "You code.")

    def factory(role):
        return text_stream(
            "查完了 @coder 改 Y" if role.id == "research" else "改完了 @you 验收"
        )

    manager = _manager(stream_fn_factory=factory)
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
    broken = _role_dir("broken")
    broken.mkdir(parents=True)
    (broken / "manifest.json").write_text(
        json.dumps({"id": "broken", "name": "Broken", "model": "no-such-model"}),
        encoding="utf-8",
    )
    (broken / "prompt.md").write_text("You break.", encoding="utf-8")

    manager = _manager(stream_fn_factory=lambda role: text_stream("ok"))
    await manager.start()
    try:
        # Roles come from configuration, so a broken one is still a member;
        # it only fails when its first round needs a runtime.
        assert "broken" in manager.role_ids
        [broken_task] = await manager.post("@broken hi")
        await _wait_for(lambda: _states(manager).get(broken_task.id) == "failed")
        assert manager.instance("broken") is None
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
    broken = _role_dir("broken")
    broken.mkdir(parents=True)
    (broken / "manifest.json").write_text(
        json.dumps({"id": "broken", "name": "Broken", "model": "no-such-model"}),
        encoding="utf-8",
    )
    (broken / "prompt.md").write_text("You break.", encoding="utf-8")

    manager = _manager(stream_fn_factory=lambda role: text_stream("ok"))
    await manager.start()
    try:
        # The broken manifest only fails when a round first needs a runtime.
        assert await manager._try_build_agent(manager.role("broken")) is None
        assert any(warning.startswith("role broken:") for warning in manager.warnings)
        (broken / "manifest.json").write_text(
            json.dumps({"id": "broken", "name": "Broken"}), encoding="utf-8"
        )
        await manager.reload()
        assert await manager._try_build_agent(manager.role("broken")) is not None
        assert not any(
            warning.startswith("role broken:") for warning in manager.warnings
        )
    finally:
        await manager.close()


async def test_close_is_safe_after_start_failure():
    _write_role("research", "You research.")
    broken = _role_dir("broken")
    broken.mkdir(parents=True)
    (broken / "manifest.json").write_text(
        json.dumps({"id": "broken", "name": "Broken", "model": "no-such-model"}),
        encoding="utf-8",
    )
    (broken / "prompt.md").write_text("You break.", encoding="utf-8")

    manager = _manager(stream_fn_factory=lambda role: text_stream("ok"))
    await manager.start()
    await manager.close()
    await manager.close()
    assert manager.agents == {}


async def test_agent_mentioning_you_raises_a_notification():
    _write_role("research", "You research.")
    frames: list[dict] = []
    manager = _manager(
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
        assert notices[0]["swarm"] is True
    finally:
        await manager.close()


async def test_user_mentioning_you_does_not_notify():
    _write_role("research", "You research.")
    frames: list[dict] = []
    manager = _manager(
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
    manager = _manager(stream_fn_factory=_slow_stream)
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
    store = SwarmStore(project_state_root(_project_root()))
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

    manager = _manager(stream_fn_factory=lambda role: text_stream("done"))
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
    store = SwarmStore(project_state_root(_project_root()))
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

    manager = _manager(stream_fn_factory=lambda role: text_stream("done"))
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
    manager = _manager(stream_fn_factory=_slow_stream)
    await manager.start()
    try:
        [task] = await manager.post("@slow work")
        await _wait_for(lambda: _states(manager)[task.id] == "running")
        await manager.update_role(
            "slow",
            {"id": "slow", "name": "Slow v2", "systemPrompt": "You are v2."},
        )
        pending = manager.instance("slow", task.work_item_id).pending_apply
        assert pending is not None
        assert pending.name == "Slow v2"
        assert "You are v1." in manager.instance("slow", task.work_item_id).agent.system_prompt

        assert await manager.cancel(task.id) is True

        def applied() -> bool:
            agent = manager.instance("slow", task.work_item_id)
            return agent is not None and agent.pending_apply is None

        await _wait_for(applied)
        assert manager.instance("slow", task.work_item_id).role.name == "Slow v2"
        assert "You are v2." in manager.instance("slow", task.work_item_id).agent.system_prompt
    finally:
        await manager.close()


async def test_delete_role_cancels_tasks():
    _write_role("victim")
    manager = _manager(stream_fn_factory=_slow_stream)
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
        assert manager.instance("victim") is None
        assert not _role_dir("victim").exists()
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
    manager = _manager(
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
    manager = _manager(
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
    manager = _manager(
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
        assert request["request_id"].startswith(f"{project_key(manager.root)}:swarm:")
        assert manager.resolve_approval(request["request_id"], "allow") is True
        await _wait_for(lambda: _states(manager)[task.id] == "done")
        root = manager.instance("writer", task.work_item_id).role.root
        assert (root / "note.txt").read_text(encoding="utf-8") == "hi"
    finally:
        await manager.close()


async def test_approval_timeout_fails_the_task(monkeypatch):
    import zenagent.app.swarm.approvals as approvals_module

    monkeypatch.setattr(approvals_module, "APPROVAL_TIMEOUT_S", 0.05)
    role_dir = _write_role("writer")
    (role_dir / "manifest.json").write_text(
        json.dumps(
            {"id": "writer", "name": "Writer", "tools": ["write_file"]}
        ),
        encoding="utf-8",
    )
    manager = _manager(
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


async def test_swarm_approval_deny_marks_task():
    role_dir = _write_role("writer")
    (role_dir / "manifest.json").write_text(
        json.dumps(
            {"id": "writer", "name": "Writer", "tools": ["write_file"]}
        ),
        encoding="utf-8",
    )
    frames: list[dict] = []
    manager = _manager(
        write_frame=frames.append,
        stream_fn_factory=lambda role: _write_then_text_stream(),
    )
    await manager.start()
    try:
        [task] = await manager.post("@writer 写个文件")
        await _wait_for(lambda: _approval_request(frames) is not None)
        request = _approval_request(frames)
        assert request is not None
        assert manager.resolve_approval(request["request_id"], "deny") is True
        # The agent finishes its turn after the refused tool, but the mutation
        # must not have run.
        await _wait_for(lambda: _states(manager)[task.id] == "done")
        root = manager.instance("writer", task.work_item_id).role.root
        assert not (root / "note.txt").exists()
    finally:
        await manager.close()


def _gated_stream(gates: dict):
    """A per-role stream that stays running until its gate Event is set."""

    def factory(role):
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
            abort = asyncio.create_task(options.abort.wait())
            gate = asyncio.create_task(gates[role.id].wait())
            done, pending = await asyncio.wait(
                {abort, gate}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            if abort in done:
                raise Aborted("cancelled")
            partial.stop_reason = "stop"
            yield AssistantEvent(type="done", partial=partial, reason="stop")

        return stream

    return factory


async def test_swarm_concurrency_cap_queues():
    _write_role("alpha")
    _write_role("beta")
    gates = {"alpha": asyncio.Event(), "beta": asyncio.Event()}
    manager = _manager(
        max_concurrent=1, stream_fn_factory=_gated_stream(gates)
    )
    await manager.start()
    try:
        [ta] = await manager.post("@alpha a")
        [tb] = await manager.post("@beta b")
        # Latch: alpha holds the only slot; beta must wait, never run alongside.
        await _wait_for(lambda: _states(manager)[ta.id] == "running")
        assert _states(manager)[tb.id] == "queued"
        assert manager.instance("beta", tb.work_item_id).state != "running"
        # Free alpha's slot; beta then acquires it and runs.
        gates["alpha"].set()
        await _wait_for(lambda: _states(manager)[ta.id] == "done")
        await _wait_for(lambda: _states(manager)[tb.id] == "running")
        gates["beta"].set()
        await _wait_for(lambda: _states(manager)[tb.id] == "done")
    finally:
        gates["alpha"].set()
        gates["beta"].set()
        await manager.close()


async def test_inbox_cap_rejects_the_overflow_posts():
    _write_role("slow")
    manager = _manager(stream_fn_factory=_slow_stream)
    await manager.start()
    try:
        [running] = await manager.post("@slow one")
        await _wait_for(lambda: _states(manager)[running.id] == "running")
        accepted = 0
        # Every follow-up quotes the same root, so they join one work item and
        # its inbox — the cap is per (role, work item), not per room.
        for index in range(17):
            accepted += len(
                await manager.post(
                    f"@slow more {index}", ref=running.source_message_id
                )
            )
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
    manager = _manager(stream_fn_factory=lambda role: text_stream("ok"))
    await manager.start()
    try:
        assert await manager._try_build_agent(manager.role("research")) is not None
        # Role sessions live under the swarm state dir by design; the runtime
        # must not mistake that for a legacy project transcript.
        assert not any("legacy (unscoped)" in warning for warning in manager.warnings)
    finally:
        await manager.close()


def _task_states(frames: list[dict], task_id: str) -> list[str]:
    return [
        str(frame.get("state"))
        for frame in frames
        if frame.get("type") == "swarm_task" and frame.get("id") == task_id
    ]


async def test_terminal_task_state_is_emitted_to_the_room():
    _write_role("research", "You research.")
    frames: list[dict] = []
    manager = _manager(
        write_frame=frames.append,
        stream_fn_factory=lambda role: text_stream("完成"),
    )
    await manager.start()
    try:
        [task] = await manager.post("@research 做")
        await _wait_for(lambda: _states(manager).get(task.id) == "done")
        assert _task_states(frames, task.id)[-1] == "done"
    finally:
        await manager.close()


async def test_cancelled_task_state_is_emitted_to_the_room():
    _write_role("slow")
    frames: list[dict] = []
    manager = _manager(write_frame=frames.append, stream_fn_factory=_slow_stream)
    await manager.start()
    try:
        [task] = await manager.post("@slow 长任务")
        await _wait_for(lambda: _states(manager).get(task.id) == "running")
        assert await manager.cancel(task.id) is True
        await _wait_for(lambda: _states(manager).get(task.id) == "cancelled")
        assert _task_states(frames, task.id)[-1] == "cancelled"
    finally:
        await manager.close()


async def test_task_transcript_replays_the_run_from_the_role_session():
    _write_role("research", "You research.")
    manager = _manager(stream_fn_factory=lambda role: text_stream("完成"))
    await manager.start()
    try:
        [task] = await manager.post("@research 做")
        await _wait_for(lambda: _states(manager).get(task.id) == "done")

        transcript = manager.task_transcript(task.id)

        assert transcript["available"] is True
        assert any(
            row["kind"] == "assistant" and row["text"] == "完成"
            for row in transcript["transcript"]
        )
    finally:
        await manager.close()


def test_task_transcript_is_unavailable_without_a_task_or_agent():
    manager = _manager()
    manager.store.tasks["t_1"] = TaskRecord(
        id="t_1", assignee="research", state="done", chain_id="c_1", chain_depth=1
    )

    assert manager.task_transcript("t_1") == {"available": False, "transcript": []}
    assert manager.task_transcript("t_missing") == {"available": False, "transcript": []}


async def test_role_events_flow_to_the_sink_stamped_with_the_active_task():
    _write_role("research", "You research.")
    seen: list[tuple[str, str, str]] = []  # (role, task, event type)

    def sink(role_id: str, task_id: str, event: object) -> None:
        seen.append((role_id, task_id, getattr(event, "type", "?")))

    manager = _manager(
        stream_fn_factory=lambda role: text_stream("done"),
        event_sink=sink,
    )
    await manager.start()
    try:
        [task] = await manager.post("@research hi")
        await _wait_for(lambda: _states(manager).get(task.id) == "done")
        assert seen, "no events reached the sink"
        assert {role for role, _, _ in seen} == {"research"}
        # every event is stamped with the run's task, never a stray id
        assert {task_id for _, task_id, _ in seen} == {task.id}
        assert "turn_start" in {etype for _, _, etype in seen}
    finally:
        await manager.close()


async def test_sink_errors_never_break_a_run_and_stray_taps_are_dropped():
    _write_role("research", "You research.")

    def boom(role_id: str, task_id: str, event: object) -> None:
        raise RuntimeError("sink exploded")

    manager = _manager(
        stream_fn_factory=lambda role: text_stream("ok"),
        event_sink=boom,
    )
    await manager.start()
    try:
        [task] = await manager.post("@research hi")
        await _wait_for(lambda: _states(manager).get(task.id) == "done")
        assert _states(manager)[task.id] == "done"
    finally:
        await manager.close()
    # a tap with no active task (between runs, after close) is dropped quietly
    tap("research", AgentEvent(type="turn_start"), sink=boom, task_id=None)


def test_subscribe_events_forwards_stamped_events_and_no_ops_without_a_sink():
    seen: list[tuple[str, str, str]] = []

    class FakeAgent:
        def __init__(self) -> None:
            self.listeners: list = []

        def subscribe(self, listener):
            self.listeners.append(listener)
            return lambda: None

    agent = FakeAgent()
    subscribe_events(
        agent,
        "research",
        sink=lambda role, task, event: seen.append((role, task, event.type)),
        task_of=lambda role: "t_9",
    )
    for listener in agent.listeners:
        listener(AgentEvent(type="turn_start"))
    assert seen == [("research", "t_9", "turn_start")]

    # No sink: nothing subscribes at all.
    quiet = FakeAgent()
    subscribe_events(quiet, "research", sink=None, task_of=lambda role: "t_9")
    assert quiet.listeners == []


def _track_runtimes(monkeypatch):
    """Record every Runtime built and closed so an orphaned runtime (built but
    never closed) is visible. Observes real lifecycle, not mock internals."""
    built: list[Runtime] = []
    closed: list[Runtime] = []
    bootstrap = Runtime.bootstrap.__func__
    close = Runtime.close

    async def tracked_bootstrap(cls, options, **kwargs):
        # Force a suspension point so concurrent rebuilds genuinely overlap;
        # without it the fake runtime's build may run to completion unyielded.
        await asyncio.sleep(0)
        runtime = await bootstrap(cls, options, **kwargs)
        built.append(runtime)
        return runtime

    async def tracked_close(self):
        closed.append(self)
        await close(self)

    monkeypatch.setattr(Runtime, "bootstrap", classmethod(tracked_bootstrap))
    monkeypatch.setattr(Runtime, "close", tracked_close)
    return built, closed


async def test_delete_role_mid_run_leaks_no_runtime(monkeypatch):
    _write_role("victim", "You are v1.")
    built, closed = _track_runtimes(monkeypatch)
    frames: list[dict] = []
    manager = _manager(write_frame=frames.append, stream_fn_factory=_slow_stream)
    await manager.start()
    try:
        [task] = await manager.post("@victim work")
        await _wait_for(lambda: _states(manager)[task.id] == "running")
        await manager.update_role(
            "victim",
            {"id": "victim", "name": "Victim v2", "systemPrompt": "You are v2."},
        )
        assert manager.instance("victim", task.work_item_id).pending_apply is not None

        before = len(frames)
        await manager.delete_role("victim")

        assert manager.instance("victim") is None
        assert _states(manager)[task.id] == "cancelled"
        # Every runtime that was built got closed — no orphan left open.
        assert set(built) == set(closed)
        # The deleted role broadcasts no agent frame after the delete began.
        assert not [
            frame
            for frame in frames[before:]
            if frame.get("type") == "swarm_agent" and frame.get("agent") == "victim"
        ]
    finally:
        await manager.close()


async def test_pending_apply_survives_a_run():
    _write_role("slow", "You are v1.")
    frames: list[dict] = []
    manager = _manager(write_frame=frames.append, stream_fn_factory=_slow_stream)
    await manager.start()
    try:
        [task] = await manager.post("@slow work")
        await _wait_for(lambda: _states(manager)[task.id] == "running")
        await manager.update_role(
            "slow",
            {"id": "slow", "name": "Slow v2", "systemPrompt": "You are v2."},
        )
        # The queued apply is advertised while the run is still in flight.
        assert any(
            frame.get("type") == "swarm_agent"
            and frame.get("agent") == "slow"
            and frame.get("pendingApply") is True
            for frame in frames
        )
        assert await manager.cancel(task.id) is True
        await _wait_for(
            lambda: manager.instance("slow", task.work_item_id) is not None
            and manager.instance("slow", task.work_item_id).pending_apply is None
        )
        assert manager.instance("slow", task.work_item_id).role.name == "Slow v2"
        assert "You are v2." in manager.instance("slow", task.work_item_id).agent.system_prompt
    finally:
        await manager.close()


async def test_concurrent_rebuilds_serialize(monkeypatch):
    _write_role("r", "You are v1.")
    built, closed = _track_runtimes(monkeypatch)
    manager = _manager(stream_fn_factory=lambda role: text_stream("ok"))
    await manager.start()
    try:
        assert await manager._try_build_agent(manager.role("r")) is not None
        await asyncio.gather(
            manager.update_role("r", {"id": "r", "name": "R v2", "systemPrompt": "v2"}),
            manager.update_role("r", {"id": "r", "name": "R v3", "systemPrompt": "v3"}),
        )
        # Serialized close+build orphans nothing: all but the current runtime closed.
        assert set(built) == set(closed) | {manager.instance("r").runtime}
        assert manager.instance("r").role.name in {"R v2", "R v3"}
    finally:
        await manager.close()


async def test_two_projects_get_isolated_rooms(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    for root, role in ((a, "alpha"), (b, "beta")):
        (root / ".zenagent" / "agents" / role).mkdir(parents=True)
        (root / ".zenagent" / "agents" / role / "manifest.json").write_text(
            json.dumps({"id": role, "name": role}), encoding="utf-8"
        )
        (root / ".zenagent" / "agents" / role / "prompt.md").write_text(
            "hi", encoding="utf-8"
        )

    ma = SwarmManager(root=a, stream_fn_factory=lambda role: text_stream("done"))
    mb = SwarmManager(root=b, stream_fn_factory=lambda role: text_stream("done"))
    await ma.start()
    await mb.start()
    try:
        assert set(ma.role_ids) == {"alpha"}
        assert set(mb.role_ids) == {"beta"}
        assert ma.store.root != mb.store.root
        alpha = await ma._try_build_agent(ma.role("alpha"))
        assert alpha is not None and alpha.role.root == a.resolve()

        [task] = await ma.post("@alpha hi")
        await _wait_for(lambda: _states(ma).get(task.id) == "done")

        texts = [message["text"] for message in ma.messages()]
        assert texts[:2] == ["@alpha hi", "done"]
        assert mb.messages() == []
        assert mb.tasks() == []
    finally:
        await ma.close()
        await mb.close()


def test_has_active_tracks_running_tasks():
    manager = _manager()
    assert manager.has_active() is False

    manager._running[("alpha", None)] = object()

    assert manager.has_active() is True


async def test_empty_project_room_points_at_the_archived_global_room():
    legacy = state_root()
    legacy.mkdir(parents=True, exist_ok=True)
    (legacy / "room.jsonl").write_text("", encoding="utf-8")

    manager = _manager(stream_fn_factory=lambda role: text_stream("ok"))
    await manager.start()
    try:
        archived = [
            warning
            for warning in manager.warnings
            if warning.startswith("legacy global room archived")
        ]
        assert archived == [
            "legacy global room archived at ~/.zenagent/swarm/; new rooms are per project"
        ]
    finally:
        await manager.close()


async def test_project_room_with_messages_does_not_warn_about_the_archive():
    legacy = state_root()
    legacy.mkdir(parents=True, exist_ok=True)
    (legacy / "room.jsonl").write_text("", encoding="utf-8")
    store = SwarmStore(project_state_root(_project_root()))
    store.load()
    store.append_message(
        RoomMessage(
            id="m_old",
            ts=1,
            author_kind="user",
            author_id="you",
            text="@worker hello",
            mentions=["worker"],
        )
    )

    manager = _manager(stream_fn_factory=lambda role: text_stream("ok"))
    await manager.start()
    try:
        assert not [
            warning
            for warning in manager.warnings
            if warning.startswith("legacy global room archived")
        ]
    finally:
        await manager.close()


async def test_unknown_ref_is_refused():
    manager = _manager()
    _write_role("research")
    await manager.start()
    try:
        with pytest.raises(ValueError, match="unknown ref"):
            await manager.post("@research hi", ref="m_missing")
    finally:
        await manager.close()


async def test_system_notice_is_not_quotable():
    manager = _manager()
    _write_role("research")
    await manager.start()
    try:
        notice = manager._system_note("note")
        assert notice.author_kind == "system"
        with pytest.raises(ValueError, match="cannot quote a system message"):
            await manager.post("@research hi", ref=notice.id)
    finally:
        await manager.close()


async def test_post_snapshots_the_quoted_message_for_every_task():
    manager = _manager()
    _write_role("research")
    _write_role("coder")
    await manager.start()
    try:
        await manager.post("@research 出结论")
        source = manager.store.messages[-1]
        tasks = await manager.post("@research @coder 复核", ref=source.id)

        assert len(tasks) == 2
        message = manager.store.messages[-1]
        assert message.ref is not None
        assert (message.ref.id, message.ref.text) == (source.id, source.text)
        assert message.ref.author_kind == source.author_kind
    finally:
        await manager.close()


async def test_images_are_refused_before_any_task_is_created(tmp_path):
    manager = _manager()
    _write_role("research")
    await manager.start()
    try:
        with pytest.raises(ValueError, match="cannot read image"):
            await manager.post("@research 看图", images=[str(tmp_path / "missing.png")])
        assert manager.tasks() == []
    finally:
        await manager.close()


async def test_a_non_vision_target_refuses_the_whole_post(tmp_path):
    manager = _manager()
    _write_role("research")
    await manager.start()
    try:
        # No runtime has been built yet (lazy build), so the vision answer is
        # config-derived; pin it as the model check would report it.
        manager._vision["research"] = False
        image = tmp_path / "a.png"
        image.write_bytes(PNG_1PX)

        with pytest.raises(ValueError, match="@research cannot accept images"):
            await manager.post("@research 看图", images=[str(image)])
        assert manager.tasks() == []
    finally:
        await manager.close()


async def test_roles_report_vision_support():
    manager = _manager()
    _write_role("research")
    await manager.start()
    try:
        assert all("supportsVision" in role for role in manager.roles())
    finally:
        await manager.close()


async def test_first_human_mention_opens_a_work_item():
    _write_role("research")
    manager = _manager(stream_fn_factory=lambda role: text_stream("ok"))
    await manager.start()
    try:
        [task] = await manager.post("@research 查一下竞品")
        item = manager.work_items.items[task.work_item_id]
        assert item.state == "open"
        assert item.opened_by_kind == "user"
        assert item.root_message_id == task.source_message_id
        assert item.rounds == 1
    finally:
        await manager.close()


async def test_quoting_a_closed_item_opens_a_continuation():
    _write_role("research")
    manager = _manager(stream_fn_factory=lambda role: text_stream("ok"))
    await manager.start()
    try:
        [first] = await manager.post("@research 查竞品")
        await _wait_for(lambda: _states(manager).get(first.id) == "done")
        item_id = first.work_item_id
        manager.work_items.update(item_id, state="closed")
        [again] = await manager.post(
            "@research 再补一条", ref=first.source_message_id
        )
        assert again.work_item_id != item_id
        fresh = manager.work_items.items[again.work_item_id]
        assert fresh.continued_from == item_id
        assert fresh.root_message_id == again.source_message_id
    finally:
        await manager.close()


async def test_agent_mention_joins_the_current_work_item():
    _write_role("research")
    _write_role("coder")
    manager = _manager(stream_fn_factory=lambda role: text_stream("ok"))
    await manager.start()
    try:
        [first] = await manager.post("@research 查竞品")
        await _wait_for(lambda: _states(manager).get(first.id) == "done")
        manager._post_from(("research", first.work_item_id), "@coder 帮我跑一下")
        delegate = next(
            task for task in manager.store.tasks.values() if task.assignee == "coder"
        )
        assert delegate.work_item_id == first.work_item_id
    finally:
        await manager.close()


async def test_agent_work_open_is_idempotent_and_leaves_a_card():
    _write_role("research")
    manager = _manager(stream_fn_factory=lambda role: text_stream("ok"))
    await manager.start()
    try:
        [first] = await manager.post("@research 查竞品")
        opened = manager.work_open(
            "这是一件新活", opened_by="research", opened_from=first.work_item_id
        )
        replayed = manager.work_open(
            "再说一次", opened_by="research", opened_from=first.work_item_id
        )
        assert replayed.id == opened.id
        assert opened.opened_from == first.work_item_id
        card = next(
            message
            for message in manager.store.messages
            if message.id == opened.root_message_id
        )
        assert card.author_kind == "system"
        assert "工单已开" in card.text
    finally:
        await manager.close()


async def test_request_close_moves_the_item_to_review():
    _write_role("research")
    manager = _manager(stream_fn_factory=lambda role: text_stream("ok"))
    await manager.start()
    try:
        [task] = await manager.post("@research 做完了")
        item = manager.request_close(task.work_item_id, "交付说明")
        assert item.state == "review"
        assert any(
            "待验收" in message.text for message in manager.store.messages
        )
    finally:
        await manager.close()
