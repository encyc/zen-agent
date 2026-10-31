"""Mouse support: the wheel must scroll the transcript wherever the pointer is."""

from textual.events import MouseScrollUp

from zenagent.cli.tui.app import ZenAgentApp
from zenagent.cli.tui.widgets.transcript import TranscriptView


async def fill_transcript(app, pilot, messages: int = 12) -> None:
    from conftest import sequenced_stream  # noqa: F401 - documents the shape

    for index in range(messages):
        await app.chat.submit(f"message {index}")
        await app.chat.wait_for_idle()
    for _ in range(3):
        await pilot.pause()


async def test_wheel_over_transcript_scrolls_up(runtime_factory):
    from conftest import sequenced_stream

    runtime = await runtime_factory(stream_fn=sequenced_stream(["x"] * 40))
    app = ZenAgentApp(runtime)
    async with app.run_test(size=(100, 20)) as pilot:
        await pilot.pause()
        await fill_transcript(app, pilot)
        transcript = app.query_one(TranscriptView)
        assert transcript.scroll_offset.y >= transcript.max_scroll_y - 1

        app.post_message(MouseScrollUp(widget=transcript, x=10, y=5, delta_x=0, delta_y=-1, button=0, shift=False, meta=False, ctrl=False, screen_x=10, screen_y=5))
        await pilot.pause()
        assert transcript.scroll_offset.y < transcript.max_scroll_y, "wheel up must move the viewport"


async def test_wheel_over_a_message_still_scrolls_the_transcript(runtime_factory):
    """The pointer usually sits over a Markdown block, not the container itself."""
    from conftest import sequenced_stream

    runtime = await runtime_factory(stream_fn=sequenced_stream(["body text"] * 40))
    app = ZenAgentApp(runtime)
    async with app.run_test(size=(100, 20)) as pilot:
        await pilot.pause()
        await fill_transcript(app, pilot)
        transcript = app.query_one(TranscriptView)
        block = app.query("Markdown").last()
        before = transcript.scroll_offset.y

        block.post_message(
            MouseScrollUp(widget=block, x=10, y=5, delta_x=0, delta_y=-1, button=0,
                          shift=False, meta=False, ctrl=False, screen_x=10, screen_y=5)
        )
        await pilot.pause()
        assert transcript.scroll_offset.y < before, "wheel over a block must bubble"


async def test_wheel_up_on_a_short_transcript_keeps_following(runtime_factory):
    """Regression: one wheel-up used to unpin the view for the whole session."""
    from conftest import sequenced_stream

    runtime = await runtime_factory(stream_fn=sequenced_stream(["ok"] * 5))
    app = ZenAgentApp(runtime)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await app.chat.submit("first")
        await app.chat.wait_for_idle()
        await pilot.pause()
        transcript = app.query_one(TranscriptView)
        block = app.query("Markdown").last()

        block.post_message(
            MouseScrollUp(widget=block, x=5, y=5, delta_x=0, delta_y=-1, button=0,
                          shift=False, meta=False, ctrl=False, screen_x=5, screen_y=5)
        )
        await pilot.pause()
        assert transcript._stick is True, "nothing scrolled, so nothing to unpin"

        for index in range(8):
            await app.chat.submit(f"more {index}")
            await app.chat.wait_for_idle()
        for _ in range(3):
            await pilot.pause()
        assert transcript.pending is False
        assert transcript.scroll_offset.y >= transcript.max_scroll_y - 1


async def test_click_on_a_markdown_block_is_harmless(runtime_factory):
    """ALLOW_SELECT is off, so the markdown selection crash path is never taken."""
    from conftest import sequenced_stream

    runtime = await runtime_factory(stream_fn=sequenced_stream(["hello **world**"]))
    app = ZenAgentApp(runtime)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await app.chat.submit("hi")
        await app.chat.wait_for_idle()
        await pilot.pause()
        await pilot.click(app.query("Markdown").last())
        await pilot.pause()
        assert app.is_running


async def test_clicking_a_tool_card_toggles_it(chat_app_with_one_tool):
    from zenagent.cli.tui.widgets.tool_card import ToolCard

    app = chat_app_with_one_tool
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await app.chat.submit("go")
        await app.chat.wait_for_idle()
        await pilot.pause()
        await pilot.pause()
        card = app.query(ToolCard).first()
        assert card.collapsed is True

        await pilot.click(card)
        await pilot.pause()
        assert card.collapsed is False


async def test_clicking_a_thinking_row_toggles_it(runtime_factory):
    from zenagent.cli.tui.widgets.rows import ThinkingRow
    from zenagent.events import AssistantEvent
    from zenagent.types import AssistantMessage, TextContent, ThinkingContent, Usage

    async def stream(model, messages, tools, options):
        partial = AssistantMessage(
            content=[], provider="p", model="m", usage=Usage(),
            stop_reason="pending", timestamp=0,
        )
        yield AssistantEvent(type="start", partial=partial)
        partial.content.append(ThinkingContent(thinking="first line\nsecond line"))
        yield AssistantEvent(type="thinking_start", partial=partial, content_index=0)
        partial.content.append(TextContent(text="done"))
        yield AssistantEvent(type="text_delta", partial=partial, content_index=1, delta="done")
        partial.stop_reason = "stop"
        yield AssistantEvent(type="done", partial=partial, reason="stop")

    runtime = await runtime_factory(stream_fn=stream)
    app = ZenAgentApp(runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await app.chat.submit("go")
        await app.chat.wait_for_idle()
        await pilot.pause()
        await pilot.pause()

        row = app.query_one(ThinkingRow)
        assert row.collapsed is True

        await pilot.click(row)
        await pilot.pause()
        assert row.collapsed is False, "a click expands the reasoning"

        await pilot.click(row)
        await pilot.pause()
        assert row.collapsed is True


async def test_wheel_that_bubbles_to_the_app_still_scrolls(runtime_factory):
    from conftest import sequenced_stream

    runtime = await runtime_factory(stream_fn=sequenced_stream(["x"] * 40))
    app = ZenAgentApp(runtime)
    async with app.run_test(size=(100, 20)) as pilot:
        await pilot.pause()
        await fill_transcript(app, pilot)
        transcript = app.query_one(TranscriptView)
        before = transcript.scroll_offset.y

        app.post_message(
            MouseScrollUp(widget=None, x=1, y=1, delta_x=0, delta_y=-1, button=0,
                          shift=False, meta=False, ctrl=False, screen_x=1, screen_y=1)
        )
        await pilot.pause()
        assert transcript.scroll_offset.y < before, "unhandled wheel scrolls the chat"


async def test_resize_keeps_a_following_transcript_at_the_tail(runtime_factory):
    from conftest import sequenced_stream

    runtime = await runtime_factory(stream_fn=sequenced_stream(["x"] * 40))
    app = ZenAgentApp(runtime)
    async with app.run_test(size=(100, 24)) as pilot:
        await pilot.pause()
        await fill_transcript(app, pilot)
        transcript = app.query_one(TranscriptView)
        assert transcript.pending is False

        await pilot.resize_terminal(100, 10)
        for _ in range(4):
            await pilot.pause()
        assert transcript._stick is True, "a resize must not abandon the tail"
        assert transcript.scroll_offset.y >= transcript.max_scroll_y - 1


async def test_sync_does_not_repaint_unchanged_history(runtime_factory):
    """Regression for the jank: sync must touch only the live tail."""
    from conftest import sequenced_stream

    runtime = await runtime_factory(stream_fn=sequenced_stream(["body"] * 30))
    app = ZenAgentApp(runtime)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        for index in range(6):
            await app.chat.submit(f"q{index}")
            await app.chat.wait_for_idle()
        await pilot.pause()

        transcript = app.query_one(TranscriptView)
        repaints = {"count": 0}
        for widget in transcript.query("Markdown"):
            original = widget.update

            def counting(renderable, _original=original, _repaints=repaints):
                _repaints["count"] += 1
                return _original(renderable)

            widget.update = counting

        transcript.sync(app.chat.transcript, app.chat.status)
        await pilot.pause()
        assert repaints["count"] == 0, "an unchanged transcript must not re-render markdown"


async def test_streaming_updates_still_land_after_many_deltas(runtime_factory):
    from zenagent.events import AssistantEvent
    from zenagent.types import AssistantMessage, TextContent, Usage

    full = " ".join(f"tok{i}" for i in range(40))

    async def stream(model, messages, tools, options):
        partial = AssistantMessage(
            content=[], provider="p", model="m", usage=Usage(),
            stop_reason="pending", timestamp=0,
        )
        yield AssistantEvent(type="start", partial=partial)
        built = ""
        for token in full.split(" "):
            built = f"{built} {token}".strip()
            partial.content = [TextContent(text=built)]
            yield AssistantEvent(type="text_delta", partial=partial, content_index=0, delta=token)
        partial.stop_reason = "stop"
        yield AssistantEvent(type="done", partial=partial, reason="stop")

    runtime = await runtime_factory(stream_fn=stream)
    app = ZenAgentApp(runtime)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await app.chat.submit("go")
        await app.chat.wait_for_idle()
        for _ in range(3):
            await pilot.pause()
        rendered = " ".join(w.source for w in app.query("Markdown"))
        assert full in rendered, "every delta must survive incremental sync"
