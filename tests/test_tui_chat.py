"""Rich chat rendering: assistant markdown and collapsible tool cards."""

from zenagent.cli.tui.widgets.chat_log import ChatLog
from zenagent.cli.tui.widgets.tool_card import ToolCard


async def test_streamed_answer_and_tool_card_render(chat_app_with_one_tool):
    app = chat_app_with_one_tool
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await app.chat.submit("go")
        await app.chat.wait_for_idle()
        await pilot.pause()
        await pilot.pause()

        assert app.query_one(ChatLog)
        cards = list(app.query(ToolCard))
        assert cards, "tool execution must produce a card"
        assert cards[0].collapsed is True

        app.set_focus(cards[0])
        await pilot.press("enter")
        await pilot.pause()
        assert cards[0].collapsed is False
