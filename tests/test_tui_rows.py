"""Transcript rows: filled user blocks, quiet thinking rows and usage tails.

omp's transcript is a sequence of typed blocks (``04-transcript-and-tools.md``):
the user message is a full-width filled bar with no prefix, thinking shows as a
single muted line until opened, and the run ends with one dim usage row.
"""

from conftest import sequenced_stream

from zenagent.cli.tui.app import ZenAgentApp
from zenagent.cli.tui.widgets.rows import ThinkingRow, UserBlock
from zenagent.cli.tui.widgets.transcript import TranscriptView


async def test_user_message_is_a_filled_block_without_prefix(runtime_factory):
    runtime = await runtime_factory(stream_fn=sequenced_stream(["ok"]))
    app = ZenAgentApp(runtime)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await app.chat.submit("hello there")
        await app.chat.wait_for_idle()
        await pilot.pause()

        block = app.query_one(UserBlock)
        assert block.plain == "hello there", "no '›' prefix, no quoting"
        assert block.region.width >= 90, "the bar spans the transcript width"


async def test_thinking_is_one_quiet_row_until_opened(runtime_factory):
    from zenagent.events import AssistantEvent
    from zenagent.types import AssistantMessage, TextContent, ThinkingContent, Usage

    async def stream(model, messages, tools, options):
        partial = AssistantMessage(
            content=[], provider="p", model="m", usage=Usage(),
            stop_reason="pending", timestamp=0,
        )
        yield AssistantEvent(type="start", partial=partial)
        partial.content.append(ThinkingContent(thinking="weighing options " * 20))
        yield AssistantEvent(type="thinking_start", partial=partial, content_index=0)
        partial.content.append(TextContent(text="done"))
        yield AssistantEvent(type="text_delta", partial=partial, content_index=1, delta="done")
        partial.stop_reason = "stop"
        yield AssistantEvent(type="done", partial=partial, reason="stop")

    runtime = await runtime_factory(stream_fn=stream)
    app = ZenAgentApp(runtime)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await app.chat.submit("think")
        await app.chat.wait_for_idle()
        await pilot.pause()

        row = app.query_one(ThinkingRow)
        assert row.collapsed is True
        assert row.region.height == 1, "collapsed thinking must be a single row"
        assert "thinking" in row.plain

        app.set_focus(row)
        await pilot.press("enter")
        await pilot.pause()
        assert row.collapsed is False
        assert "weighing options" in row.plain


async def test_transcript_follows_new_output_only_while_at_bottom(runtime_factory):
    from conftest import sequenced_stream

    runtime = await runtime_factory(stream_fn=sequenced_stream(["one", "two"]))
    app = ZenAgentApp(runtime)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        transcript = app.query_one(TranscriptView)

        await app.chat.submit("first")
        await app.chat.wait_for_idle()
        await pilot.pause()
        assert transcript.pending is False, "a followed transcript never pends"

        # Simulate the user pinning the viewport by parking it above the tail.
        transcript._stick = False
        await app.chat.submit("second")
        await app.chat.wait_for_idle()
        await pilot.pause()
        assert transcript.pending is True
        assert app.query_one("#new-content").display is True

        transcript.scroll_to_bottom()
        await pilot.pause()
        assert transcript.pending is False
        assert app.query_one("#new-content").display is False


async def test_thinking_row_is_not_duplicated_when_it_arrives_mid_stream(runtime_factory):
    from zenagent.events import AssistantEvent
    from zenagent.types import AssistantMessage, TextContent, ThinkingContent, Usage

    async def stream(model, messages, tools, options):
        partial = AssistantMessage(
            content=[], provider="p", model="m", usage=Usage(),
            stop_reason="pending", timestamp=0,
        )
        yield AssistantEvent(type="start", partial=partial)
        partial.content.append(ThinkingContent(thinking="hmm"))
        yield AssistantEvent(type="thinking_start", partial=partial, content_index=0)
        partial.content.append(TextContent(text="ok"))
        yield AssistantEvent(type="text_delta", partial=partial, content_index=1, delta="ok")
        partial.stop_reason = "stop"
        yield AssistantEvent(type="done", partial=partial, reason="stop")

    runtime = await runtime_factory(stream_fn=stream)
    app = ZenAgentApp(runtime)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await app.chat.submit("go")
        await app.chat.wait_for_idle()
        await pilot.pause()
        assert len(app.query(ThinkingRow)) == 1
