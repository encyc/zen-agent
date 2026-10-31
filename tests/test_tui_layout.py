"""P0 layout: single column transcript over a sticky composer dock.

These tests are the spec for the omp-inspired layout (see
``docs/research/omp-cli-study/02-layout.md``):

    transcript (1fr, scrolls)
    status band (1 row, filled, above the prompt)
    prompt row (unboxed, ``╰─ `` gutter on the first line only)

The status band sits *above* the input and the input is the very last row --
there is no bottom status bar and no sidebar by default.
"""

from zenagent.cli.tui.app import ZenAgentApp
from zenagent.cli.tui.widgets.prompt_gutter import PromptGutter
from zenagent.cli.tui.widgets.prompt_input import PromptInput
from zenagent.cli.tui.widgets.status_band import StatusBand
from zenagent.cli.tui.widgets.transcript import TranscriptView
from zenagent.cli.tui.widgets.welcome import WelcomeBlock


async def test_dock_order_is_transcript_then_band_then_prompt(fake_runtime):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        transcript = app.query_one(TranscriptView)
        band = app.query_one(StatusBand)
        prompt = app.query_one(PromptInput)

        assert transcript.size.height > 0
        assert band.region.y + band.region.height == prompt.region.y
        assert prompt.region.y + prompt.region.height == 40
        assert transcript.region.y + transcript.region.height <= band.region.y


async def test_status_band_is_a_single_filled_row(fake_runtime):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        band = app.query_one(StatusBand)
        assert band.size.height == 1
        assert not band.styles.border_top[0], "the band must stay frameless"


async def test_prompt_is_unboxed_and_uses_curved_gutter(fake_runtime):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        prompt = app.query_one(PromptInput)
        gutter = app.query_one(PromptGutter)

        assert app.query_one("#prompt-row").size.height == prompt.size.height
        assert gutter.size.width == 3
        assert gutter.size.height == prompt.size.height
        assert "╰─" in gutter.plain


async def test_sidebar_is_hidden_by_default_and_toggles(fake_runtime):
    from zenagent.cli.tui.widgets.sidebar import Sidebar

    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        sidebar = app.query_one(Sidebar)
        assert sidebar.display is False

        await pilot.press("ctrl+b")
        await pilot.pause()
        assert sidebar.display is True
        await pilot.press("ctrl+b")
        await pilot.pause()
        assert sidebar.display is False


async def test_welcome_block_opens_the_transcript(fake_runtime):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        welcome = app.query_one(WelcomeBlock)
        assert "zen" in welcome.plain
        assert welcome.region.y < app.query_one(StatusBand).region.y


async def test_prompt_grows_and_gutter_follows(fake_runtime):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        prompt = app.query_one(PromptInput)
        gutter = app.query_one(PromptGutter)
        base = prompt.size.height

        app.set_focus(prompt)
        for _ in range(3):
            await pilot.press("shift+enter")
        await pilot.pause()
        await pilot.pause()

        assert prompt.size.height == base + 3
        assert gutter.size.height == prompt.size.height


async def test_escape_keeps_the_draft_and_ctrl_c_clears_it(fake_runtime):
    """omp: Esc never destroys a draft; ctrl+c clears it; ctrl+d exits."""
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        prompt = app.query_one(PromptInput)
        prompt.focus()
        for char in "hello":
            await pilot.press(char)
        await pilot.pause()

        await pilot.press("escape")
        await pilot.pause()
        assert prompt.text == "hello", "Esc must not touch the draft"

        await pilot.press("ctrl+c")
        await pilot.pause()
        assert prompt.text == ""
        assert app.is_running, "ctrl+c is not exit"


async def test_ctrl_q_queues_while_running(gated_app):
    app, gate = gated_app
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await app.chat.submit("hi")
        await pilot.pause()
        prompt = app.query_one(PromptInput)
        prompt.focus()
        for char in "later":
            await pilot.press(char)
        await pilot.press("ctrl+q")
        await pilot.pause()
        assert app.chat.status.follow_ups == 1
        assert prompt.text == ""

        gate.set()
        await app.chat.wait_for_idle()


def _todo_event(phases):
    from zenagent.agent.loop import ToolResultAgent
    from zenagent.events import AgentEvent
    from zenagent.types import TextContent

    return AgentEvent(
        type="tool_execution_end",
        tool_call_id="t1",
        tool_name="todo",
        result=ToolResultAgent(
            content=[TextContent(text="todo")],
            details={"op": "init", "phases": phases},
        ),
    )


def _phases():
    return [
        {
            "name": "Explore",
            "tasks": [{"content": "read a", "status": "in_progress"}],
        }
    ]


async def test_todo_has_exactly_one_visible_home(fake_runtime):
    """Sidebar visible owns the todo; collapsing hands it back to the dock HUD."""
    from zenagent.cli.tui.widgets.sidebar import Sidebar
    from zenagent.cli.tui.widgets.todo_hud import TodoHud

    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        app.chat._on_event(_todo_event(_phases()))
        app._refresh_status()
        await pilot.pause()

        hud = app.query_one(TodoHud)
        sidebar = app.query_one(Sidebar)
        assert sidebar.display is False
        assert hud.display is True

        await pilot.press("ctrl+b")
        await pilot.pause()
        assert sidebar.display is True
        assert hud.display is False, "the dock HUD must yield to the sidebar"

        await pilot.press("ctrl+b")
        await pilot.pause()
        assert sidebar.display is False
        assert hud.display is True


async def test_sidebar_toggle_is_ignored_on_narrow_terminals(fake_runtime):
    from zenagent.cli.tui.widgets.sidebar import Sidebar
    from zenagent.cli.tui.widgets.todo_hud import TodoHud

    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(90, 30)) as pilot:
        await pilot.pause()
        app.chat._on_event(_todo_event(_phases()))
        app._refresh_status()
        await pilot.pause()

        await pilot.press("ctrl+b")
        await pilot.pause()

        assert app.query_one(Sidebar).display is False
        assert app.query_one(TodoHud).display is True
