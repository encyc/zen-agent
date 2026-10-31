"""The TUI side of the ask tool: framed one-question-at-a-time dialogs.

The app registers itself as the process-wide ask handler while mounted; the
modal path mirrors ``tests/test_tui_approval.py`` (poll until the dialog is laid
out, then drive it with the pilot).
"""

import asyncio

import pytest

from zenagent.cli.tui.screens.modals import AskQuestion, DialogChoice
from zenagent.tools.ask import ASK, get_ask_handler, set_ask_handler


def question(
    question_id="q1",
    *,
    header="Storage",
    text="Which database should the service use?",
    multi=False,
    recommended="SQLite",
    options=None,
):
    payload = {
        "id": question_id,
        "question": text,
        "options": options
        or [
            {"label": "SQLite", "description": "zero-config, local"},
            {"label": "Postgres", "description": "needs a server"},
        ],
        "multi": multi,
    }
    if header:
        payload["header"] = header
    if recommended:
        payload["recommended"] = recommended
    return payload


@pytest.fixture(autouse=True)
def clean_handler():
    set_ask_handler(None)
    yield
    set_ask_handler(None)


async def _wait_for_ask(
    app,
    *,
    previous: object | None = None,
    expect=AskQuestion,
    laid_out: bool = True,
):
    """Poll until a new dialog screen is mounted (and has a real panel size)."""
    for _ in range(500):
        screen = app.screen
        if isinstance(screen, expect) and screen is not previous:
            if not laid_out:
                return screen
            panels = list(screen.query("#dialog"))
            if panels and panels[0].region.width > 0:
                return screen
        await asyncio.sleep(0.01)
    raise AssertionError("ask dialog never appeared (or never laid out)")


def choices(screen):
    return list(screen.query(DialogChoice))


# ---------------------------------------------------------------------------
# handler registration lifecycle
# ---------------------------------------------------------------------------


async def test_mounting_the_app_registers_the_ask_handler(picker_app):
    app = picker_app
    assert get_ask_handler() is None, "no frontend owns the UI yet"

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        handler = get_ask_handler()
        assert handler is not None
        # The registered handler is the app's public request_ask method.
        assert handler.__self__ is app

    assert get_ask_handler() is None, "unmount must clear the handler"


# ---------------------------------------------------------------------------
# one dialog, single choice
# ---------------------------------------------------------------------------


async def test_request_ask_returns_the_selected_label(picker_app):
    app = picker_app
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        task = asyncio.create_task(app.request_ask([question()]))
        screen = await _wait_for_ask(app)

        postgres = next(
            choice for choice in choices(screen) if "Postgres" in choice.plain
        )
        await pilot.click(postgres)

        assert await asyncio.wait_for(task, 2) == [
            {"id": "q1", "selected": ["Postgres"]}
        ]


async def test_number_keys_choose_an_option(picker_app):
    app = picker_app
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        task = asyncio.create_task(app.request_ask([question()]))
        await _wait_for_ask(app)

        await pilot.press("2")

        assert await asyncio.wait_for(task, 2) == [
            {"id": "q1", "selected": ["Postgres"]}
        ]


async def test_the_dialog_is_a_framed_panel_with_context(picker_app):
    from textual.widgets import Static

    app = picker_app
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        task = asyncio.create_task(app.request_ask([question()]))
        screen = await _wait_for_ask(app)

        panel = screen.query_one("#dialog")
        assert panel.region.width >= 40, "the dialog must be a real panel"

        title = screen.query_one("#dialog-title", Static)
        assert "Storage" in title.content.plain

        body = screen.query_one(".dialog-detail", Static)
        assert "Which database" in body.content.plain

        rows = choices(screen)
        assert [row.plain for row in rows] == [
            "[1] SQLite (Recommended)  — zero-config, local",
            "[2] Postgres  — needs a server",
        ]
        hint = screen.query_one("#dialog-hint", Static)
        assert "choose" in hint.content.plain
        assert "esc" in hint.content.plain

        await pilot.press("1")
        assert await asyncio.wait_for(task, 2) == [
            {"id": "q1", "selected": ["SQLite"]}
        ]


async def test_escape_cancels_the_whole_ask(picker_app):
    app = picker_app
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        task = asyncio.create_task(app.request_ask([question()]))
        await _wait_for_ask(app)

        await pilot.press("escape")

        assert await asyncio.wait_for(task, 2) == []


async def test_enter_does_not_answer_by_accident(picker_app):
    app = picker_app
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        task = asyncio.create_task(app.request_ask([question()]))
        await _wait_for_ask(app)

        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, AskQuestion), "a stray Enter must not answer"

        await pilot.press("1")
        assert await asyncio.wait_for(task, 2) == [
            {"id": "q1", "selected": ["SQLite"]}
        ]


async def test_dismissing_later_questions_keeps_earlier_answers(picker_app):
    app = picker_app
    second = question("q2", header="Cache", text="Which cache?")
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        task = asyncio.create_task(app.request_ask([question(), second]))
        first_screen = await _wait_for_ask(app)
        await pilot.press("1")

        second_screen = await _wait_for_ask(app, previous=first_screen)
        assert "Cache" in second_screen.query_one("#dialog-title").content.plain
        await pilot.press("escape")

        assert await asyncio.wait_for(task, 2) == [
            {"id": "q1", "selected": ["SQLite"]}
        ]


async def test_questions_are_asked_one_at_a_time_in_order(picker_app):
    app = picker_app
    second = question("q2", header="Cache", text="Which cache?")
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        task = asyncio.create_task(app.request_ask([question(), second]))

        first_screen = await _wait_for_ask(app)
        assert "Storage" in first_screen.query_one("#dialog-title").content.plain
        await pilot.press("1")

        second_screen = await _wait_for_ask(app, previous=first_screen)
        assert "Cache" in second_screen.query_one("#dialog-title").content.plain
        await pilot.press("2")

        assert await asyncio.wait_for(task, 2) == [
            {"id": "q1", "selected": ["SQLite"]},
            {"id": "q2", "selected": ["Postgres"]},
        ]


# ---------------------------------------------------------------------------
# multi-select
# ---------------------------------------------------------------------------


async def test_multi_select_toggles_rows_and_submits(picker_app):
    app = picker_app
    multi = question(multi=True, recommended=None)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        task = asyncio.create_task(app.request_ask([multi]))
        screen = await _wait_for_ask(app)

        rows = choices(screen)
        assert not any("✓" in row.plain for row in rows)
        await pilot.press("1")
        await pilot.pause()
        await pilot.press("2")
        await pilot.pause()
        assert "✓" in rows[0].plain and "✓" in rows[1].plain

        hint = screen.query_one("#dialog-hint").content.plain
        assert "toggle" in hint and "submit" in hint

        await pilot.press("enter")
        assert await asyncio.wait_for(task, 2) == [
            {"id": "q1", "selected": ["SQLite", "Postgres"]}
        ]


async def test_multi_select_can_toggle_a_row_back_off(picker_app):
    app = picker_app
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        task = asyncio.create_task(
            app.request_ask([question(multi=True, recommended=None)])
        )
        screen = await _wait_for_ask(app)

        await pilot.press("1")
        await pilot.pause()
        await pilot.press("1")
        await pilot.pause()
        assert "✓" not in choices(screen)[0].plain

        await pilot.press("enter")
        assert await asyncio.wait_for(task, 2) == [{"id": "q1", "selected": []}]


# ---------------------------------------------------------------------------
# tool + app wiring
# ---------------------------------------------------------------------------


async def test_the_ask_tool_uses_the_mounted_tui_handler(picker_app):
    app = picker_app
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        task = asyncio.create_task(
            ASK.execute("call_ask", {"questions": [question()]}, None)
        )
        await _wait_for_ask(app)
        await pilot.press("1")

        result = await asyncio.wait_for(task, 2)
        assert not result.is_error
        text = result.content[0].text
        assert "User answers:" in text and "SQLite" in text
        assert result.details["available"] is True
        assert result.details["answers"] == [{"id": "q1", "selected": ["SQLite"]}]


async def test_ask_tool_falls_back_after_the_app_unmounts(runtime_factory):
    from zenagent.cli.tui.app import ZenAgentApp

    runtime = await runtime_factory()
    app = ZenAgentApp(runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert get_ask_handler() is not None

    assert get_ask_handler() is None
    result = await ASK.execute("call_ask", {"questions": [question()]}, None)
    assert not result.is_error
    assert result.details == {"available": False}


async def test_a_model_issued_ask_shows_the_dialog_and_feeds_the_answer(runtime_factory):
    """Full path: agent loop → ask tool → mounted TUI dialog → tool result."""
    from zenagent.cli.tui.app import ZenAgentApp
    from zenagent.events import AssistantEvent
    from zenagent.types import (
        AssistantMessage,
        TextContent,
        ToolCall,
        ToolResultMessage,
        Usage,
    )

    async def stream(model, messages, tools, options):
        partial = AssistantMessage(
            content=[],
            provider="p",
            model="m",
            usage=Usage(),
            stop_reason="pending",
            timestamp=0,
        )
        yield AssistantEvent(type="start", partial=partial)
        if isinstance(messages[-1], ToolResultMessage):
            partial.content = [TextContent(text="done")]
            partial.stop_reason = "stop"
            yield AssistantEvent(
                type="text_delta", partial=partial, content_index=0, delta="done"
            )
            yield AssistantEvent(type="done", partial=partial, reason="stop")
            return
        call = ToolCall(id="c1", name="ask", arguments={"questions": [question()]})
        partial.content = [call]
        yield AssistantEvent(
            type="toolcall_start", partial=partial, content_index=0, tool_call=call
        )
        yield AssistantEvent(
            type="toolcall_end", partial=partial, content_index=0, tool_call=call
        )
        partial.stop_reason = "tool_use"
        yield AssistantEvent(type="done", partial=partial, reason="tool_use")

    runtime = await runtime_factory(stream_fn=stream)
    app = ZenAgentApp(runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await app.chat.submit("go")
        await _wait_for_ask(app)
        await pilot.press("1")
        await app.chat.wait_for_idle()
        await pilot.pause()
        await pilot.pause()

        cards = [
            view for view in app.chat.transcript if getattr(view, "name", "") == "ask"
        ]
        assert cards, "the ask call must produce a tool card"
        assert "User answers:" in cards[0].result_text
        assert "SQLite" in cards[0].result_text
