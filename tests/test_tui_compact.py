"""TUI compaction: the transcript divider and the summary that replaces it."""

from zenagent.cli.tui.app import ZenAgentApp
from zenagent.cli.tui.widgets.prompt_input import PromptInput
from zenagent.cli.tui.widgets.rows import DividerRow, UserBlock


async def test_slash_compact_shows_a_divider_and_the_summary(fake_runtime):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        await app.chat.submit("hi")
        await app.chat.wait_for_idle()
        await pilot.pause()

        prompt = app.query_one(PromptInput)
        prompt.focus()
        prompt.insert("/compact")
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

        dividers = list(app.query(DividerRow))
        assert dividers and "compacted" in dividers[0].plain
        # The summary is a UserMessage, so it renders as a filled user block.
        assert any("此前会话摘要" in block.plain for block in app.query(UserBlock))
