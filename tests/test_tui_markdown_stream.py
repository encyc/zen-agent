"""Streaming Markdown: only the growing tail is re-parsed.

Textual's ``Markdown.update`` throws every block away and re-mounts the whole
document, so a token stream costs O(document) per delta and leaves detached
paragraphs answering hit tests for a turn (the click crash ``on_event`` guards).
``StreamingMarkdown`` appends the tail instead: the paragraph the user is
pointing at stays the same widget.
"""

from textual.app import App, ComposeResult

from zenagent.cli.tui.app import ZenAgentApp
from zenagent.cli.tui.widgets.markdown_stream import StreamingMarkdown


class Host(App):
    """A one-widget app: the stream is the only thing under test."""

    def __init__(self, text: str = "") -> None:
        super().__init__()
        self.document = StreamingMarkdown(text)

    def compose(self) -> ComposeResult:
        yield self.document


def _paragraphs(app: Host) -> list[str]:
    return [block._content.plain for block in app.document.query("MarkdownParagraph")]


async def test_growing_text_updates_the_paragraph_in_place():
    app = Host("alpha")
    async with app.run_test(size=(60, 20)) as pilot:
        await pilot.pause()
        paragraph = app.document.query("MarkdownParagraph").first()
        assert paragraph._content.plain == "alpha"

        app.document.stream_text("alpha beta gamma")
        await app.document.wait_for_render()

        assert app.document.query("MarkdownParagraph").first() is paragraph
        assert paragraph._content.plain == "alpha beta gamma"
        assert app.document.source == "alpha beta gamma"


async def test_a_new_paragraph_is_appended_without_rebuilding_the_first():
    app = Host("first")
    async with app.run_test(size=(60, 20)) as pilot:
        await pilot.pause()
        first = app.document.query("MarkdownParagraph").first()

        app.document.stream_text("first\n\nsecond")
        await app.document.wait_for_render()

        assert app.document.query("MarkdownParagraph").first() is first
        assert _paragraphs(app) == ["first", "second"]


async def test_a_rewritten_answer_replaces_the_document():
    """The canonical message can differ from the deltas; there is no tail then."""
    app = Host("alpha beta")
    async with app.run_test(size=(60, 20)) as pilot:
        await pilot.pause()

        app.document.stream_text("gamma")
        await app.document.wait_for_render()

        assert app.document.source == "gamma"
        assert _paragraphs(app) == ["gamma"]


async def test_a_burst_of_deltas_collapses_into_one_append():
    app = Host("")
    async with app.run_test(size=(60, 20)) as pilot:
        await pilot.pause()
        appended: list[str] = []
        append = app.document.append

        def counting(delta: str):
            appended.append(delta)
            return append(delta)

        app.document.append = counting
        # Every delta arrives before the pump gets to run: one render, newest text.
        for size in range(1, 41):
            app.document.stream_text("x" * size)
        await app.document.wait_for_render()

        assert appended == ["x" * 40]
        assert app.document.source == "x" * 40


def _delta_stream(chunks: list[str]):
    """A provider that streams ``chunks`` as text deltas, then the whole answer."""

    async def stream(model, messages, tools, options):
        from zenagent.events import AssistantEvent
        from zenagent.types import AssistantMessage, TextContent, Usage

        full = "".join(chunks)
        partial = AssistantMessage(
            content=[TextContent(text="")],
            provider="p",
            model="m",
            usage=Usage(),
            stop_reason="pending",
            timestamp=0,
        )
        yield AssistantEvent(type="start", partial=partial)
        yield AssistantEvent(type="text_start", partial=partial, content_index=0)
        for chunk in chunks:
            partial.content[0].text += chunk
            yield AssistantEvent(
                type="text_delta", partial=partial, content_index=0, delta=chunk
            )
        partial.content[0].text = full
        partial.stop_reason = "stop"
        yield AssistantEvent(type="done", partial=partial, reason="stop")

    return stream


async def test_the_transcript_streams_deltas_into_one_paragraph(runtime_factory):
    chunks = ["the answer ", "arrives ", "in pieces"]
    runtime = await runtime_factory(stream_fn=_delta_stream(chunks))
    app = ZenAgentApp(runtime)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await app.chat.submit("hi")
        await app.chat.wait_for_idle()
        await pilot.pause()

        answer = app.query(StreamingMarkdown).last()
        await answer.wait_for_render()

        assert answer.source == "".join(chunks)
        assert _paragraphs_text(answer) == ["the answer arrives in pieces"]
        assert app.is_running


def _paragraphs_text(widget) -> list[str]:
    return [block._content.plain for block in widget.query("MarkdownParagraph")]


async def test_a_streamed_code_fence_grows_in_place():
    """The chunk that matters most: a fence is rebuilt by ``update`` every delta."""
    app = Host("")
    chunks = ["Here:\n\n```python\n", "print(1)\n", "print(2)\n", "```\n\nDone."]
    async with app.run_test(size=(60, 20)) as pilot:
        await pilot.pause()
        text = ""
        fence = None
        for chunk in chunks:
            text += chunk
            app.document.stream_text(text)
            await app.document.wait_for_render()
            await pilot.pause()
            current = app.document.query("MarkdownFence").last()
            if fence is not None:
                assert current is fence, "the open fence is updated, not re-mounted"
            fence = current

        assert app.document.source == text
        assert fence.code == "print(1)\nprint(2)"
        assert _paragraphs(app) == ["Here:", "Done."]


async def test_text_that_arrives_before_the_mount_renders_after_it():
    """The transcript mounts a widget and keeps streaming into it in one turn."""
    app = Host("alpha")
    app.document.stream_text("alpha beta")  # no DOM yet: the pump must wait

    async with app.run_test(size=(60, 20)) as pilot:
        await pilot.pause()
        await app.document.wait_for_render()
        assert app.document.source == "alpha beta"
        assert _paragraphs(app) == ["alpha beta"]
