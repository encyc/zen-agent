import asyncio
import time

import pytest
from conftest import text_stream, tool_then_text_stream

from zenagent.app.swarm.roles import RoleManifest
from zenagent.app.swarm.room import (
    RoomMessage,
    SwarmStore,
    TaskRecord,
    parse_mentions,
)
from zenagent.app.swarm.router import Router
from zenagent.app.swarm.runner import TaskRunner
from zenagent.events import Aborted, AssistantEvent
from zenagent.types import (
    AssistantMessage,
    Cost,
    TextContent,
    Usage,
)

ROLE = RoleManifest(id="research", name="Research")


def _bind(tmp_path):
    store = SwarmStore(tmp_path)
    router = Router(store, {"research", "coder"})
    current: dict = {"task": None}

    def post(text: str, kind: str) -> RoomMessage:
        task = current["task"]
        message = RoomMessage(
            id=store.new_id("m"),
            ts=int(time.time()),
            author_kind=kind,
            author_id="research" if kind == "agent" else "system",
            text=text,
            mentions=parse_mentions(text) if kind == "agent" else [],
            task_id=task.id if task is not None else None,
            chain_depth=task.chain_depth if task is not None else 0,
        )
        store.append_message(message)
        if kind == "agent":
            router.admit(message, inbox_sizes={})
        return message

    return store, router, current, post


def _message(store: SwarmStore, text: str = "@research 查一下") -> RoomMessage:
    message = RoomMessage(
        id=store.new_id("m"),
        ts=int(time.time()),
        author_kind="user",
        author_id="you",
        text=text,
        mentions=parse_mentions(text),
    )
    store.append_message(message)
    return message


def _task(store: SwarmStore, **overrides) -> TaskRecord:
    fields = {
        "id": store.new_id("t"),
        "assignee": "research",
        "state": "queued",
        "source_message_id": "m0",
        "created_by_kind": "user",
        "created_by_id": "you",
        "chain_id": store.new_id("c"),
        "chain_depth": 0,
        "chain_cost": 0.0,
        "created_at": 1,
        "updated_at": 1,
    }
    fields.update(overrides)
    task = TaskRecord(**fields)
    store.append_task(task)
    return task


def _runner(runtime, store, post) -> TaskRunner:
    return TaskRunner(
        store=store,
        agent=runtime.agent,
        session=runtime.session,
        role=ROLE,
        post_message=post,
    )


async def test_final_text_is_posted_and_task_done(tmp_path, runtime_factory):
    runtime = await runtime_factory(stream_fn=text_stream("结论是 Z"))
    store, _router, current, post = _bind(tmp_path)
    task = _task(store)
    current["task"] = task

    record = await _runner(runtime, store, post).run(
        task, _message(store)
    )

    assert record.state == "done"
    assert store.tasks[task.id].state == "done"
    assert record.result_message_id is not None
    posted = next(m for m in store.messages if m.id == record.result_message_id)
    assert posted.author_kind == "agent"
    assert posted.author_id == "research"
    assert posted.text == "结论是 Z"
    assert posted.task_id == task.id


async def test_mentions_in_result_create_tasks(tmp_path, runtime_factory):
    runtime = await runtime_factory(stream_fn=text_stream("结论是 Z。@coder 改 Y"))
    store, _router, current, post = _bind(tmp_path)
    task = _task(store)
    current["task"] = task

    record = await _runner(runtime, store, post).run(
        task, _message(store)
    )

    coder_tasks = [entry for entry in store.tasks.values() if entry.assignee == "coder"]
    assert len(coder_tasks) == 1
    assert coder_tasks[0].source_message_id == record.result_message_id
    assert coder_tasks[0].chain_depth == 1
    assert coder_tasks[0].chain_id == task.chain_id


async def test_max_turns_marks_failed(tmp_path, runtime_factory):
    runtime = await runtime_factory(
        stream_fn=tool_then_text_stream(), max_turns=1
    )
    store, _router, current, post = _bind(tmp_path)
    task = _task(store)
    current["task"] = task

    record = await _runner(runtime, store, post).run(
        task, _message(store)
    )

    assert record.state == "failed"
    assert record.reason == "max_turns"
    assert store.tasks[task.id].state == "failed"
    assert any(
        message.author_kind == "system" and task.id in message.text
        for message in store.messages
    )


async def test_abort_marks_cancelled(tmp_path, runtime_factory):
    async def abortable(model, messages, tools, options):
        partial = AssistantMessage(
            content=[],
            provider="p",
            model="m",
            usage=Usage(),
            stop_reason="pending",
            timestamp=0,
        )
        yield AssistantEvent(type="start", partial=partial)
        while not options.abort.aborted:
            await asyncio.sleep(0.001)
        raise Aborted("cancelled by user")

    runtime = await runtime_factory(stream_fn=abortable)
    store, _router, current, post = _bind(tmp_path)
    task = _task(store)
    current["task"] = task
    runner = _runner(runtime, store, post)

    running = asyncio.create_task(runner.run(task, _message(store)))
    await asyncio.sleep(0.05)
    runtime.agent.abort()
    record = await running

    assert record.state == "cancelled"
    assert store.tasks[task.id].state == "cancelled"


async def test_chain_cost_accumulates(tmp_path, runtime_factory):
    def cost_stream(total: float):
        async def stream(model, messages, tools, options):
            partial = AssistantMessage(
                content=[TextContent(text="done")],
                provider="p",
                model="m",
                usage=Usage(cost=Cost(total=total)),
                stop_reason="pending",
                timestamp=0,
            )
            yield AssistantEvent(type="start", partial=partial)
            partial.stop_reason = "stop"
            yield AssistantEvent(type="done", partial=partial, reason="stop")

        return stream

    runtime = await runtime_factory(stream_fn=cost_stream(0.4))
    store, _router, current, post = _bind(tmp_path)
    task = _task(store, chain_cost=1.0)
    current["task"] = task

    record = await _runner(runtime, store, post).run(
        task, _message(store)
    )

    assert record.chain_cost == pytest.approx(1.4)
    assert store.tasks[task.id].chain_cost == pytest.approx(1.4)
