"""ChatController's pending-message view model and its queue lane.

The TUI renders ``RunStatus.pending`` above the composer; these tests pin the
data path: a submit while a run is in flight becomes a visible ``queue`` entry,
dequeue takes the last one back (restoring its images), and clearing reports
how many entries disappeared.
"""

import asyncio

from zenagent.app.chat import ChatController
from zenagent.events import AssistantEvent
from zenagent.types import AssistantMessage, ImageContent, TextContent, Usage


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
