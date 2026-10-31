"""Thinking rows show whether the model is reasoning right now and for how long.

The row ticks while a reasoning block is open and freezes at the total when the
answer (or the run) ends. History replay has no timing, so those rows stay bare.
"""

import asyncio
import re

import pytest

from zenagent.cli.tui.app import ZenAgentApp
from zenagent.cli.tui.widgets.rows import ThinkingRow, format_thinking_duration


def test_duration_formatting():
    assert format_thinking_duration(0.42) == "0.4s"
    assert format_thinking_duration(3.26) == "3.3s"
    assert format_thinking_duration(12.4) == "12s"
    assert format_thinking_duration(59.6) == "1m 00s"
    assert format_thinking_duration(125.0) == "2m 05s"


@pytest.fixture
async def gated_thinking_app(runtime_factory):
    gate = asyncio.Event()

    async def stream(model, messages, tools, options):
        from zenagent.events import AssistantEvent
        from zenagent.types import (
            AssistantMessage,
            TextContent,
            ThinkingContent,
            Usage,
        )

        partial = AssistantMessage(
            content=[], provider="p", model="m", usage=Usage(),
            stop_reason="pending", timestamp=0,
        )
        yield AssistantEvent(type="start", partial=partial)
        partial.content.append(ThinkingContent(thinking="weighing options"))
        yield AssistantEvent(type="thinking_start", partial=partial, content_index=0)
        yield AssistantEvent(
            type="thinking_delta",
            partial=partial,
            content_index=0,
            delta="weighing options",
        )
        await gate.wait()
        yield AssistantEvent(type="thinking_end", partial=partial, content_index=0)
        partial.content.append(TextContent(text="done"))
        yield AssistantEvent(
            type="text_delta", partial=partial, content_index=1, delta="done"
        )
        partial.stop_reason = "stop"
        yield AssistantEvent(type="done", partial=partial, reason="stop")

    runtime = await runtime_factory(stream_fn=stream)
    return ZenAgentApp(runtime), gate


async def test_the_row_ticks_while_reasoning_then_freezes(gated_thinking_app):
    app, gate = gated_thinking_app
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await app.chat.submit("go")
        for _ in range(6):
            await pilot.pause()

        row = app.query_one(ThinkingRow)
        assert row.active is True, "the model is still reasoning"
        assert "thinking…" in row.plain
        assert "weighing options" in row.plain, "the preview streams before the answer"
        assert re.fullmatch(r"\d+(\.\d)?s", row.duration_text or "")
        assert row.region.height == 1

        painted = row.content.plain
        await asyncio.sleep(ThinkingRow.TICK + 0.25)
        await pilot.pause()
        assert row.content.plain != painted, "the live duration repaints while active"

        gate.set()
        await app.chat.wait_for_idle()
        await pilot.pause()
        await pilot.pause()

        assert row.active is False
        assert "thinking…" not in row.plain
        assert row.duration_text is not None
        view = app.chat.transcript[-1]
        assert view.thinking_started_at is None
        assert view.thinking_seconds is not None and view.thinking_seconds >= 0


def test_history_replay_has_no_timer():
    from zenagent.app.views import messages_to_views
    from zenagent.types import AssistantMessage, ThinkingContent, Usage

    message = AssistantMessage(
        content=[ThinkingContent(thinking="from history")],
        provider="p",
        model="m",
        usage=Usage(),
        stop_reason="stop",
        timestamp=0,
    )

    view = messages_to_views([message])[-1]

    assert view.thinking == "from history"
    assert view.thinking_started_at is None
    assert view.thinking_seconds is None
