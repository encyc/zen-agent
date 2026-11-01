"""ChatController's pending-message view model and its queue lane.

The TUI renders ``RunStatus.pending`` above the composer; these tests pin the
data path: a submit while a run is in flight becomes a visible ``queue`` entry,
dequeue takes the last one back (restoring its images), and clearing reports
how many entries disappeared.
"""

import asyncio

from conftest import text_stream

from zenagent.app.chat import ChatController
from zenagent.app.views import PendingMessage
from zenagent.events import Aborted, AssistantEvent
from zenagent.types import (
    AssistantMessage,
    ImageContent,
    TextContent,
    ToolCall,
    Usage,
    UserMessage,
)


def gated_stream(gate: asyncio.Event):
    async def stream(model, messages, tools, options):
        partial = AssistantMessage(
            content=[TextContent(text="")],
            provider="p",
            model="m",
            usage=Usage(),
            stop_reason="pending",
            timestamp=0,
        )
        yield AssistantEvent(type="start", partial=partial)
        await gate.wait()
        partial.content[0].text = "done"
        partial.stop_reason = "stop"
        yield AssistantEvent(
            type="text_delta", partial=partial, content_index=0, delta="done"
        )
        yield AssistantEvent(type="done", partial=partial, reason="stop")

    return stream


def gated_tool_stream(gate: asyncio.Event):
    """First turn asks for ``read_file`` and waits at ``gate``; then answers."""

    calls = {"n": 0}

    async def stream(model, messages, tools, options):
        calls["n"] += 1
        partial = AssistantMessage(
            content=[],
            provider="p",
            model="m",
            usage=Usage(),
            stop_reason="pending",
            timestamp=0,
        )
        yield AssistantEvent(type="start", partial=partial)
        if calls["n"] == 1:
            call = ToolCall(
                id="c1", name="read_file", arguments={"path": "README.md"}
            )
            partial.content = [call]
            yield AssistantEvent(
                type="toolcall_end", partial=partial, tool_call=call
            )
            await gate.wait()
            partial.stop_reason = "tool_use"
            yield AssistantEvent(type="done", partial=partial, reason="tool_use")
        else:
            partial.content = [TextContent(text="done")]
            partial.stop_reason = "stop"
            yield AssistantEvent(
                type="text_delta", partial=partial, content_index=0, delta="done"
            )
            yield AssistantEvent(type="done", partial=partial, reason="stop")

    return stream


async def test_second_submit_becomes_a_visible_queue_entry(runtime_factory):
    gate = asyncio.Event()
    runtime = await runtime_factory(stream_fn=gated_stream(gate))
    chat = ChatController(runtime)
    task = asyncio.create_task(chat.submit("hi"))
    await asyncio.sleep(0.05)

    await chat.submit("follow up")

    assert [(p.lane, p.text) for p in chat.status.pending] == [
        ("queue", "follow up")
    ]
    assert chat.status.follow_ups == 1

    gate.set()
    await task
    await chat.wait_for_idle()


async def test_dequeue_returns_the_last_queue_entry_and_restores_images(
    runtime_factory,
):
    gate = asyncio.Event()
    runtime = await runtime_factory(stream_fn=gated_stream(gate))
    chat = ChatController(runtime)
    task = asyncio.create_task(chat.submit("hi"))
    await asyncio.sleep(0.05)

    chat.attach_image(ImageContent(data="aGk=", mime_type="image/png"))
    chat.queue_follow_up("one")   # takes the attached image
    chat.queue_follow_up("two")
    assert [(p.text, p.images) for p in chat.status.pending] == [
        ("one", 1),
        ("two", 0),
    ]

    assert chat.dequeue_follow_up() == "two"
    assert [p.text for p in chat.status.pending] == ["one"]
    assert chat.attachments == []

    assert chat.dequeue_follow_up() == "one"
    assert len(chat.attachments) == 1   # images come back to the composer
    assert chat.dequeue_follow_up() is None

    gate.set()
    await task
    await chat.wait_for_idle()


async def test_clear_pending_returns_the_cleared_count(runtime_factory):
    gate = asyncio.Event()
    runtime = await runtime_factory(stream_fn=gated_stream(gate))
    chat = ChatController(runtime)
    task = asyncio.create_task(chat.submit("hi"))
    await asyncio.sleep(0.05)

    chat.queue_follow_up("one")
    chat.queue_follow_up("two")

    assert chat.clear_pending() == 2
    assert chat.status.pending == []
    assert chat.status.follow_ups == 0

    gate.set()
    await task
    await chat.wait_for_idle()


async def test_pending_ids_increase_and_survive_dequeue(runtime_factory):
    gate = asyncio.Event()
    runtime = await runtime_factory(stream_fn=gated_stream(gate))
    chat = ChatController(runtime)
    task = asyncio.create_task(chat.submit("hi"))
    await asyncio.sleep(0.05)

    chat.queue_follow_up("one")
    chat.queue_follow_up("two")
    ids = [p.id for p in chat.status.pending]
    chat.dequeue_follow_up()
    chat.queue_follow_up("three")

    assert ids[0] < ids[1]
    assert chat.status.pending[-1].id > ids[1]

    gate.set()
    await task
    await chat.wait_for_idle()


async def test_steer_while_streaming_shows_in_the_steer_lane(runtime_factory):
    gate = asyncio.Event()
    runtime = await runtime_factory(stream_fn=gated_stream(gate))
    chat = ChatController(runtime)
    task = asyncio.create_task(chat.submit("hi"))
    await asyncio.sleep(0.05)

    chat.steer("actually, check the logs")

    assert [(p.lane, p.text) for p in chat.status.pending] == [
        ("steer", "actually, check the logs")
    ]
    assert runtime.agent.pending_steering == 1

    gate.set()
    await task


async def test_promote_moves_a_queued_entry_to_the_steer_lane(runtime_factory):
    gate = asyncio.Event()
    runtime = await runtime_factory(stream_fn=gated_stream(gate))
    chat = ChatController(runtime)
    task = asyncio.create_task(chat.submit("hi"))
    await asyncio.sleep(0.05)

    chat.queue_follow_up("use the staging config")
    entry = chat.status.pending[0]

    assert chat.promote(entry.id) == "ok"
    assert [(p.lane, p.text) for p in chat.status.pending] == [
        ("steer", "use the staging config")
    ]
    assert runtime.agent.pending_steering == 1

    gate.set()
    await task


async def test_promote_rejects_entries_with_images(runtime_factory):
    gate = asyncio.Event()
    runtime = await runtime_factory(stream_fn=gated_stream(gate))
    chat = ChatController(runtime)
    task = asyncio.create_task(chat.submit("hi"))
    await asyncio.sleep(0.05)

    chat.attach_image(ImageContent(data="aGk=", mime_type="image/png"))
    chat.queue_follow_up("with a screenshot")
    entry = chat.status.pending[0]

    assert chat.promote(entry.id) == "blocked_images"
    assert [p.lane for p in chat.status.pending] == ["queue"]
    assert runtime.agent.pending_steering == 0

    gate.set()
    await task


async def test_promote_when_idle_is_not_running(runtime_factory):
    runtime = await runtime_factory(stream_fn=text_stream())
    chat = ChatController(runtime)
    chat.queue_follow_up("later")
    entry = chat.status.pending[0]

    assert chat.promote(entry.id) == "not_running"
    assert [p.lane for p in chat.status.pending] == ["queue"]
    assert chat.promote(999) == "missing"


async def test_steer_when_idle_falls_back_to_the_queue(runtime_factory):
    runtime = await runtime_factory(stream_fn=text_stream())
    chat = ChatController(runtime)

    chat.steer("later")

    assert [(p.lane, p.text) for p in chat.status.pending] == [
        ("queue", "later")
    ]
    assert runtime.agent.pending_steering == 0


async def test_injected_steering_clears_the_mirror_and_lands_in_transcript(
    runtime_factory,
):
    gate = asyncio.Event()
    runtime = await runtime_factory(stream_fn=gated_tool_stream(gate))
    chat = ChatController(runtime)
    task = asyncio.create_task(chat.submit("check the readme"))
    await asyncio.sleep(0.05)

    chat.steer("actually, check the logs")
    assert [(p.lane, p.text) for p in chat.status.pending] == [
        ("steer", "actually, check the logs")
    ]
    gate.set()
    await task
    await chat.wait_for_idle()

    assert chat.status.pending == []
    assert runtime.agent.pending_steering == 0
    users = [m for m in runtime.agent.messages if isinstance(m, UserMessage)]
    assert any("actually, check the logs" in str(m.content) for m in users)


async def test_stranded_steering_continues_the_run(runtime_factory):
    gate = asyncio.Event()
    calls: list[list] = []

    async def stream(model, messages, tools, options):
        calls.append(list(messages))
        partial = AssistantMessage(
            content=[TextContent(text="")], provider="p", model="m",
            usage=Usage(), stop_reason="pending", timestamp=0,
        )
        yield AssistantEvent(type="start", partial=partial)
        await gate.wait()
        partial.content = [TextContent(text=f"answer{len(calls)}")]
        partial.stop_reason = "stop"
        yield AssistantEvent(type="done", partial=partial, reason="stop")

    runtime = await runtime_factory(stream_fn=stream)
    chat = ChatController(runtime)
    task = asyncio.create_task(chat.submit("hi"))
    await asyncio.sleep(0.05)

    chat.steer("also add tests")   # 本轮没有工具轮，插话等不到边界
    gate.set()
    await task
    await chat.wait_for_idle()

    assert len(calls) == 2, "the stranded steer must trigger a continuation run"
    assert chat.status.running is False
    assert chat.status.pending == []
    users = [m for m in runtime.agent.messages if isinstance(m, UserMessage)]
    assert any("also add tests" in str(m.content) for m in users)


async def test_error_ending_demotes_steering_to_the_queue(runtime_factory):
    gate = asyncio.Event()
    calls: list[list] = []
    states: list[tuple[bool, list[PendingMessage], str]] = []

    async def stream(model, messages, tools, options):
        calls.append(list(messages))
        partial = AssistantMessage(
            content=[TextContent(text="")], provider="p", model="m",
            usage=Usage(), stop_reason="pending", timestamp=0,
        )
        yield AssistantEvent(type="start", partial=partial)
        await gate.wait()
        if len(calls) == 1:
            partial.stop_reason = "error"
            yield AssistantEvent(type="error", partial=partial, reason="error")
        else:
            partial.content = [TextContent(text="carried on")]
            partial.stop_reason = "stop"
            yield AssistantEvent(type="done", partial=partial, reason="stop")

    runtime = await runtime_factory(stream_fn=stream)
    chat = ChatController(
        runtime,
        on_change=lambda: states.append(
            (chat.status.running, list(chat.status.pending), chat.status.last_error)
        ),
    )
    task = asyncio.create_task(chat.submit("hi"))
    await asyncio.sleep(0.05)

    chat.steer("save the logs")
    gate.set()
    await task
    await chat.wait_for_idle()

    demoted = [
        state
        for state in states
        if state[2] and state[1] and all(p.lane == "queue" for p in state[1])
    ]
    assert demoted, "the steer must become a visible queue entry before the next run"
    assert len(calls) == 2
    assert chat.status.pending == []
    users = [m for m in runtime.agent.messages if isinstance(m, UserMessage)]
    assert any("save the logs" in str(m.content) for m in users)


async def test_aborted_run_keeps_steering_as_a_visible_queue_entry(runtime_factory):
    gate = asyncio.Event()
    calls: list[list] = []

    async def stream(model, messages, tools, options):
        calls.append(list(messages))
        partial = AssistantMessage(
            content=[TextContent(text="")], provider="p", model="m",
            usage=Usage(), stop_reason="pending", timestamp=0,
        )
        yield AssistantEvent(type="start", partial=partial)
        await gate.wait()
        if options.abort is not None and options.abort.aborted:
            raise Aborted("cancelled by user")
        partial.stop_reason = "stop"
        yield AssistantEvent(type="done", partial=partial, reason="stop")

    runtime = await runtime_factory(stream_fn=stream)
    chat = ChatController(runtime)
    task = asyncio.create_task(chat.submit("hi"))
    await asyncio.sleep(0.05)

    chat.steer("save the logs")
    chat.abort()
    gate.set()
    await task
    await chat.wait_for_idle()

    assert len(calls) == 1, "an aborted run must not auto-continue"
    assert chat.status.running is False
    assert [(p.lane, p.text) for p in chat.status.pending] == [
        ("queue", "save the logs")
    ]


async def test_continuation_runs_before_auto_compaction(runtime_factory, monkeypatch):
    import zenagent.app.chat as chat_module

    gate = asyncio.Event()
    compacted: list[list[str]] = []
    calls: list[list] = []

    async def stream(model, messages, tools, options):
        calls.append(list(messages))
        partial = AssistantMessage(
            content=[TextContent(text="")], provider="p", model="m",
            usage=Usage(), stop_reason="pending", timestamp=0,
        )
        yield AssistantEvent(type="start", partial=partial)
        await gate.wait()
        partial.content = [TextContent(text=f"answer{len(calls)}")]
        partial.stop_reason = "stop"
        yield AssistantEvent(type="done", partial=partial, reason="stop")

    runtime = await runtime_factory(stream_fn=stream)
    chat = ChatController(runtime)
    monkeypatch.setattr(chat_module, "should_auto_compact", lambda *a, **k: True)

    async def fake_compact(force: bool = False) -> str | None:
        compacted.append([p.lane for p in chat.status.pending])
        return None

    monkeypatch.setattr(chat, "compact_now", fake_compact)

    task = asyncio.create_task(chat.submit("hi"))
    await asyncio.sleep(0.05)
    chat.steer("also add tests")
    gate.set()
    await task
    await chat.wait_for_idle()

    assert compacted == [[]], "compaction must wait until the pending lanes are empty"
