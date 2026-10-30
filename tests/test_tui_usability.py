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
        assert len(hints.matches) >= 15, "every command is matchable"
        assert "more" in hints.plain, "the window says what it is holding back"


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
        assert "mode" in hints.plain and "model" in hints.plain
        assert "sessions" not in hints.plain

        await pilot.press("down", "tab")
        await pilot.pause()
        assert prompt.text == "/model "
        # Completing the command reveals that command's argument values.
        assert "deepseek-flash" in hints.plain


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


async def test_every_registered_command_is_reachable_from_the_hint_list(fake_runtime):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        names = {command.name for command in app.commands.names()}
        expected = {"model", "sessions", "thinking", "settings", "help", "new", "quit"}
        assert expected <= names, f"missing commands: {expected - names}"

        # The list is searchable, so even commands past the window are reachable.
        prompt = app.query_one(PromptInput)
        prompt.focus()
        for char in "/sett":
            await pilot.press(char)
        await pilot.pause()
        hints = app.query_one(CommandHints)
        assert "settings" in [name for name, _ in hints.matches]


async def test_settings_palette_opens(fake_runtime):
    from zenagent.cli.tui.screens.modals import SettingsPicker

    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await app._dispatch_command("settings", "")
        await pilot.pause()
        assert isinstance(app.screen, SettingsPicker)


async def test_ctrl_t_expands_and_collapses_thinking(runtime_factory):
    from zenagent.cli.tui.widgets.rows import ThinkingRow
    from zenagent.events import AssistantEvent
    from zenagent.types import AssistantMessage, TextContent, ThinkingContent, Usage

    async def stream(model, messages, tools, options):
        partial = AssistantMessage(
            content=[], provider="p", model="m", usage=Usage(),
            stop_reason="pending", timestamp=0,
        )
        yield AssistantEvent(type="start", partial=partial)
        partial.content.append(ThinkingContent(thinking="step one, step two"))
        yield AssistantEvent(type="thinking_start", partial=partial, content_index=0)
        partial.content.append(TextContent(text="done"))
        yield AssistantEvent(type="text_delta", partial=partial, content_index=1, delta="done")
        partial.stop_reason = "stop"
        yield AssistantEvent(type="done", partial=partial, reason="stop")

    runtime = await runtime_factory(stream_fn=stream)
    app = ZenAgentApp(runtime)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        prompt = app.query_one(PromptInput)
        prompt.focus()
        await app.chat.submit("go")
        await app.chat.wait_for_idle()
        await pilot.pause()

        row = app.query_one(ThinkingRow)
        assert row.collapsed is True
        assert row.size.height == 1

        await pilot.press("ctrl+t")
        await pilot.pause()
        assert row.collapsed is False
        assert "step one, step two" in row.plain

        await pilot.press("ctrl+t")
        await pilot.pause()
        assert row.collapsed is True


async def test_work_indicator_switches_with_the_run(gated_app):
    from zenagent.cli.tui.widgets.work_indicator import WorkIndicator

    app, gate = gated_app
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        indicator = app.query_one(WorkIndicator)
        assert indicator.display is False, "idle shows nothing"

        await app.chat.submit("hi")
        await pilot.pause()
        assert indicator.display is True and indicator.running is True
        assert "working" in indicator.plain

        await pilot.press("escape")
        gate.set()
        await app.chat.wait_for_idle()
        await pilot.pause()
        assert indicator.running is False


async def test_mode_command_updates_runtime_and_status(picker_app):
    app = picker_app
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await app._dispatch_command("mode", "plan")
        await pilot.pause()
        assert app.runtime.mode == "plan"
        assert app.chat.status.mode == "plan"


async def test_mode_argument_hints_list_the_values(fake_runtime):
    from zenagent.cli.tui.app import ZenAgentApp

    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        prompt = app.query_one(PromptInput)
        prompt.focus()
        for char in "/mode":
            await pilot.press(char)
        await pilot.press("space")
        await pilot.pause()
        hints = app.query_one(CommandHints)
        assert "plan" in hints.plain and "ask" in hints.plain and "full" in hints.plain
        assert "/mode" not in hints.plain  # value hints, not command names

        await pilot.press("tab")
        await pilot.pause()
        assert prompt.text == "/mode plan "


async def test_argument_hint_filters_then_completes(fake_runtime):
    from zenagent.cli.tui.app import ZenAgentApp

    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        prompt = app.query_one(PromptInput)
        prompt.focus()
        for char in "/thinking":
            await pilot.press(char)
        await pilot.press("space", "h")
        await pilot.pause()
        hints = app.query_one(CommandHints)
        assert "high" in hints.plain and "medium" not in hints.plain

        await pilot.press("tab")
        await pilot.pause()
        assert prompt.text == "/thinking high "


async def test_bare_mode_command_opens_the_selector(fake_runtime):
    from zenagent.cli.tui.app import ZenAgentApp
    from zenagent.cli.tui.screens.modals import ChoicePicker

    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await app._dispatch_command("mode", "")
        await pilot.pause()
        assert isinstance(app.screen, ChoicePicker)
        await pilot.press("down", "enter")
        await pilot.pause()
        await pilot.pause()
        assert app.runtime.mode == "full"


async def test_bare_thinking_command_opens_the_selector(fake_runtime):
    from zenagent.cli.tui.app import ZenAgentApp
    from zenagent.cli.tui.screens.modals import ChoicePicker

    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await app._dispatch_command("thinking", "")
        await pilot.pause()
        assert isinstance(app.screen, ChoicePicker)
        await pilot.press("down", "enter")
        await pilot.pause()
        await pilot.pause()
        assert app.runtime.agent.thinking == "max"


async def test_enter_on_a_value_hint_accepts_and_submits(fake_runtime):
    """Enter on a highlighted value applies it, without a detour through the picker."""
    from zenagent.cli.tui.app import ZenAgentApp
    from zenagent.cli.tui.screens.modals import ChoicePicker

    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        prompt = app.query_one(PromptInput)
        prompt.focus()
        for char in "/mode":
            await pilot.press(char)
        await pilot.press("space")
        await pilot.pause()
        await pilot.press("down", "down")  # plan -> ask -> full
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        assert not isinstance(app.screen, ChoicePicker), "no redundant submenu"
        assert app.runtime.mode == "full"
        assert prompt.text == ""


async def test_enter_on_a_command_hint_reaches_the_command_picker(fake_runtime):
    """A bare command (no value yet) still opens its selector after Enter."""
    from zenagent.cli.tui.app import ZenAgentApp
    from zenagent.cli.tui.screens.modals import ChoicePicker

    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        prompt = app.query_one(PromptInput)
        prompt.focus()
        for char in "/mode":
            await pilot.press(char)
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        assert isinstance(app.screen, ChoicePicker)
        assert prompt.text == ""


async def test_palette_commands_dispatch_through_the_registry(fake_runtime):
    from zenagent.cli.tui.app import ZenAgentApp
    from zenagent.cli.tui.widgets.rows import NoticeRow

    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app._run_command("tools")
        await pilot.pause()
        await pilot.pause()
        assert any("read_file" in row.plain for row in app.query(NoticeRow))
