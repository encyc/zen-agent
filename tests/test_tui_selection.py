"""Mouse text selection in the transcript: Textual's own selection path.

`ALLOW_SELECT` is on, so dragging over a Markdown block selects its text and
`ctrl+c` copies the selection instead of clearing the draft (the same split omp
gives `tui.input.copy`). These tests pin that, because the switch was off for
months after a Textual 8.2 crash that no longer reproduces.
"""

from conftest import sequenced_stream

from zenagent.cli.tui.app import ZenAgentApp
from zenagent.cli.tui.widgets.prompt_input import PromptInput

ANSWER = "alpha beta gamma delta epsilon zeta eta theta"


async def _answered_app(runtime_factory):
    runtime = await runtime_factory(stream_fn=sequenced_stream([ANSWER]))
    app = ZenAgentApp(runtime)
    return app


async def _drag_over_the_answer(app, pilot) -> None:
    block = app.query("Markdown").last()
    await pilot.mouse_down(block, offset=(1, 0))
    await pilot.pause()
    await pilot.hover(block, offset=(30, 0))
    await pilot.pause()
    await pilot.mouse_up(block, offset=(30, 0))
    await pilot.pause()


async def test_dragging_over_a_markdown_block_selects_text(runtime_factory):
    app = await _answered_app(runtime_factory)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await app.chat.submit("hi")
        await app.chat.wait_for_idle()
        await pilot.pause()

        await _drag_over_the_answer(app, pilot)

        selected = app.screen.get_selected_text()
        assert selected is not None and selected.startswith("alpha beta")
        assert app.is_running


async def test_ctrl_c_copies_the_selection_and_keeps_the_draft(runtime_factory):
    app = await _answered_app(runtime_factory)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await app.chat.submit("hi")
        await app.chat.wait_for_idle()
        await pilot.pause()

        prompt = app.query_one(PromptInput)
        prompt.text = "draft text"
        prompt.focus()
        await pilot.pause()

        await _drag_over_the_answer(app, pilot)
        await pilot.press("ctrl+c")
        await pilot.pause()

        assert app.clipboard.startswith("alpha beta"), "ctrl+c must copy the selection"
        assert prompt.text == "draft text", "a copy must not clear the draft"


async def test_ctrl_c_without_a_selection_still_clears_the_draft(runtime_factory):
    app = await _answered_app(runtime_factory)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await app.chat.submit("hi")
        await app.chat.wait_for_idle()
        await pilot.pause()

        prompt = app.query_one(PromptInput)
        prompt.text = "draft text"
        prompt.focus()
        await pilot.pause()

        await pilot.press("ctrl+c")
        await pilot.pause()

        assert prompt.text == "", "no selection means the old clear-draft behaviour"


async def test_dragging_while_the_answer_streams_does_not_crash(runtime_factory):
    """The selected widget is rewritten mid-drag; that must not tear the app."""
    app = await _answered_app(runtime_factory)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await app.chat.submit("hi")
        await pilot.pause()
        await _drag_over_the_answer(app, pilot)

        await app.chat.wait_for_idle()
        for _ in range(3):
            await pilot.pause()

        assert app.screen.get_selected_text()
        assert app.is_running
