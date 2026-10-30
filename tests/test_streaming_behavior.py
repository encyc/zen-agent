"""Streaming regression tests: deltas must reach the app while the run is live.

The message-level retry wrapper used to buffer every assistant call, which
silently disabled streaming in every frontend (TUI, REPL, headless). These tests
fail if that buffering comes back on the default path (``retries=0``).
"""

from __future__ import annotations

import asyncio

from zenagent.app.chat import ChatController
from zenagent.events import AssistantEvent
from zenagent.types import AssistantMessage, TextContent, Usage


def _partial() -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text="")],
        provider="p",
        model="m",
        usage=Usage(),
        stop_reason="pending",
        timestamp=0,
    )


async def test_chat_controller_sees_a_delta_before_the_stream_finishes(runtime_factory):
    release = asyncio.Event()
    saw_delta = asyncio.Event()

    async def stream(model, messages, tools, options):
        partial = _partial()
        yield AssistantEvent(type="start", partial=partial)
        partial.content[0].text += "hello"
        yield AssistantEvent(
            type="text_delta", partial=partial, content_index=0, delta="hello"
        )
        await release.wait()
        partial.stop_reason = "stop"
        yield AssistantEvent(type="done", partial=partial, reason="stop")

    runtime = await runtime_factory(stream_fn=stream)

    chat: ChatController | None = None

    def on_change() -> None:
        if chat is None:
            return
        if any(
            getattr(view, "kind", "") == "assistant"
            and getattr(view, "text", "") == "hello"
            for view in chat.transcript
        ):
            saw_delta.set()

    chat = ChatController(runtime, on_change=on_change)
    await chat.submit("go")
    try:
        # This times out when the retry wrapper buffers the whole call.
        await asyncio.wait_for(saw_delta.wait(), timeout=2.0)
    finally:
        release.set()
    await chat.wait_for_idle()

    assistant = [
        view for view in chat.transcript if getattr(view, "kind", "") == "assistant"
    ]
    assert [view.text for view in assistant] == ["hello"]
