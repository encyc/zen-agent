"""Regressions from real-terminal use: slash hints, scrolling and prompt reset."""

from zenagent.cli.tui.app import ZenAgentApp
from zenagent.cli.tui.widgets.command_hints import CommandHints
from zenagent.cli.tui.widgets.prompt_input import PromptInput
from zenagent.cli.tui.widgets.transcript import TranscriptView


async def test_typing_slash_lists_commands(fake_runtime):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        prompt = app.query_one(PromptInput)
        prompt.focus()
        await pilot.press("/")
        await pilot.pause()

        hints = app.query_one(CommandHints)
        assert hints.display is True
        assert "/clear" in hints.plain, "the list opens on the first match"
        assert len(hints.matches) == 6, "bounded list"


async def test_slash_hints_filter_and_complete(fake_runtime):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        prompt = app.query_one(PromptInput)
        prompt.focus()

        for char in "/mo":
            await pilot.press(char)
        await pilot.pause()
        hints = app.query_one(CommandHints)
        assert "model" in hints.plain and "sessions" not in hints.plain

        await pilot.press("tab")
        await pilot.pause()
        assert prompt.text == "/model "
        assert hints.display is False


async def test_hints_hide_when_not_a_command(fake_runtime):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        prompt = app.query_one(PromptInput)
        prompt.focus()
        for char in "hello":
            await pilot.press(char)
        await pilot.pause()
        assert app.query_one(CommandHints).display is False


async def test_submitting_resets_the_prompt_to_one_row(fake_runtime):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        prompt = app.query_one(PromptInput)
        prompt.focus()
        for char in "hello":
            await pilot.press(char)
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

        assert prompt.text == ""
        assert prompt.size.height == 1, "the prompt must shrink back to one row"


async def test_transcript_scrolls_with_page_keys_while_prompt_is_focused(runtime_factory):
    from conftest import sequenced_stream

    runtime = await runtime_factory(stream_fn=sequenced_stream(["x"] * 40))
    app = ZenAgentApp(runtime)
    async with app.run_test(size=(100, 20)) as pilot:
        await pilot.pause()
        prompt = app.query_one(PromptInput)
        prompt.focus()
        for index in range(6):
            await app.chat.submit(f"message {index}")
            await app.chat.wait_for_idle()
        for _ in range(3):
            await pilot.pause()

        transcript = app.query_one(TranscriptView)
        assert transcript.scroll_offset.y >= transcript.max_scroll_y - 1, "follows the tail"

        await pilot.press("pageup")
        for _ in range(2):
            await pilot.pause()
        assert transcript.scroll_offset.y < transcript.max_scroll_y - 1, "pageup scrolls up"
        assert transcript.pending is False, "a manual scroll is not 'new output'"

        await pilot.press("pagedown")
        for _ in range(3):
            await pilot.pause()
        assert transcript.scroll_offset.y >= transcript.max_scroll_y - 1

        # Back at the tail, new output must be followed again.
        await app.chat.submit("after scrolling back")
        await app.chat.wait_for_idle()
        for _ in range(3):
            await pilot.pause()
        assert transcript.scroll_offset.y >= transcript.max_scroll_y - 1
