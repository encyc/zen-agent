"""ChatController: streaming into view models, follow-up queue, steer, abort."""

import asyncio

from zenagent.app.chat import ChatController
from zenagent.events import AssistantEvent
from zenagent.types import AssistantMessage, TextContent, Usage


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


async def test_submit_streams_and_second_submit_queues(runtime_factory):
    gate = asyncio.Event()
    runtime = await runtime_factory(stream_fn=gated_stream(gate))
    changes: list[int] = []
    chat = ChatController(runtime, on_change=lambda: changes.append(1))

    task = asyncio.create_task(chat.submit("hi"))
    await asyncio.sleep(0.05)
    assert chat.status.running is True

    await chat.submit("follow up")  # running → queued
    assert chat.status.follow_ups == 1

    gate.set()
    await task
    await asyncio.sleep(0.1)  # queued message auto-sent after the first run

    assert changes
    assert chat.status.follow_ups == 0
    assert chat.transcript[0].text == "hi"
    assert chat.transcript[1].text == "done"


async def test_status_reports_cache_write_and_last_turn_timing(runtime_factory):
    async def stream(model, messages, tools, options):
        partial = AssistantMessage(
            content=[],
            provider="p",
            model="m",
            usage=Usage(input=100, output=20, cache_read=60, cache_write=40),
            stop_reason="pending",
            timestamp=0,
        )
        yield AssistantEvent(type="start", partial=partial)
        await asyncio.sleep(0.02)
        partial.content = [TextContent(text="hi")]
        partial.stop_reason = "stop"
        yield AssistantEvent(
            type="text_delta", partial=partial, content_index=0, delta="hi"
        )
        yield AssistantEvent(type="done", partial=partial, reason="stop")

    runtime = await runtime_factory(stream_fn=stream)
    chat = ChatController(runtime)
    await chat.submit("hi")
    await chat.wait_for_idle()

    status = chat.status
    assert status.cache_write == 40
    assert status.turn_ms >= 15 and status.turn_output_tokens == 20
