"""Rich chat rendering: assistant markdown and collapsible tool cards."""

from zenagent.cli.tui.app import ZenAgentApp
from zenagent.cli.tui.widgets.tool_card import ToolCard
from zenagent.cli.tui.widgets.transcript import TranscriptView


async def test_streamed_answer_and_tool_card_render(chat_app_with_one_tool):
    app = chat_app_with_one_tool
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await app.chat.submit("go")
        await app.chat.wait_for_idle()
        await pilot.pause()
        await pilot.pause()

        assert app.query_one(TranscriptView)
        cards = list(app.query(ToolCard))
        assert cards, "tool execution must produce a card"
        assert cards[0].collapsed is True

        app.set_focus(cards[0])
        await pilot.press("enter")
        await pilot.pause()
        assert cards[0].collapsed is False


async def test_two_turns_render_into_separate_bubbles(runtime_factory):
    from conftest import sequenced_stream

    runtime = await runtime_factory(stream_fn=sequenced_stream(["first", "second"]))
    app = ZenAgentApp(runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await app.chat.submit("one")
        await app.chat.wait_for_idle()
        await app.chat.submit("two")
        await app.chat.wait_for_idle()
        await pilot.pause()
        texts = [w.source for w in app.query("Markdown")]
        assert "first" in " ".join(texts) and "second" in " ".join(texts)


async def test_chat_log_reset_clears_widgets(runtime_factory):
    from conftest import sequenced_stream

    runtime = await runtime_factory(stream_fn=sequenced_stream(["first"]))
    app = ZenAgentApp(runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await app.chat.submit("one")
        await app.chat.wait_for_idle()
        await pilot.pause()
        log = app.query_one(TranscriptView)
        assert len(app.query("Markdown")) == 1
        log.reset()
        await pilot.pause()
        assert len(app.query("Markdown")) == 0


async def test_mouse_click_on_markdown_does_not_crash(runtime_factory):
    """Textual 8.2's selection path crashes when clicking a Markdown paragraph."""
    from conftest import sequenced_stream

    runtime = await runtime_factory(stream_fn=sequenced_stream(["hello **world**"]))
    app = ZenAgentApp(runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await app.chat.submit("hi")
        await app.chat.wait_for_idle()
        await pilot.pause()
        await pilot.click(app.query_one("Markdown"))
        await pilot.pause()
        assert app.is_running


async def test_collapsed_tool_card_is_one_line(chat_app_with_one_tool):
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
        assert card.region.height <= 2, f"collapsed card took {card.region.height} rows"


async def test_tool_card_repaints_on_streamed_progress(runtime_factory, monkeypatch):
    """A progress update (status still running) must refresh the live card."""
    from conftest import text_stream

    from zenagent.agent.loop import ToolResultAgent
    from zenagent.cli.tui.widgets.tool_card import ToolCard
    from zenagent.events import AgentEvent
    from zenagent.types import TextContent

    refreshed: list[str] = []
    original_refresh = ToolCard.refresh_view

    def spy(self) -> None:
        refreshed.append(self.view.call_id)
        original_refresh(self)

    monkeypatch.setattr(ToolCard, "refresh_view", spy)

    runtime = await runtime_factory(stream_fn=text_stream())
    app = ZenAgentApp(runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.chat._on_event(
            AgentEvent(
                type="tool_execution_start",
                tool_call_id="t1",
                tool_name="bash",
                args={"command": "make"},
            )
        )
        await pilot.pause()
        app.chat._on_event(
            AgentEvent(
                type="tool_execution_update",
                tool_call_id="t1",
                tool_name="bash",
                args={"command": "make"},
                partial_result=ToolResultAgent(
                    content=[TextContent(text="compiling\nlinking 2 files")]
                ),
            )
        )
        await pilot.pause()
        await pilot.pause()
        cards = list(app.query(ToolCard))
        assert cards, "tool_execution_start should mount a card"
        assert "t1" in refreshed, "the live card must be repainted on progress"
        assert "linking 2 files" in cards[0].plain
