"""Read-only subagent view: clicking a sidebar row shows the child transcript.

The parent transcript is swapped for a second ``TranscriptView`` while viewing;
the prompt is disabled (there is nothing to talk to), esc returns, and the
parent keeps syncing behind the scenes so returning shows everything that
happened meanwhile.
"""


from zenagent.agent.loop import ToolResultAgent
from zenagent.cli.tui.app import ZenAgentApp
from zenagent.cli.tui.widgets.prompt_input import PromptInput
from zenagent.cli.tui.widgets.sidebar import Sidebar, SidebarRow
from zenagent.cli.tui.widgets.subagent_banner import SubagentBanner
from zenagent.cli.tui.widgets.transcript import TranscriptView
from zenagent.events import AgentEvent
from zenagent.types import AssistantMessage, TextContent, Usage, UserMessage


def _assistant_partial(text: str = "") -> AssistantMessage:
    return AssistantMessage(
        content=[] if not text else [TextContent(text=text)],
        provider="p",
        model="m",
        usage=Usage(),
        stop_reason="pending",
        timestamp=1,
    )


def _child_events() -> list[AgentEvent]:
    partial = _assistant_partial()
    return [
        AgentEvent(type="message_start", message=UserMessage("research this", 0)),
        AgentEvent(type="message_start", message=partial),
        AgentEvent(
            type="tool_execution_start",
            tool_call_id="tc1",
            tool_name="read_file",
            args={"path": "a.py"},
        ),
        AgentEvent(
            type="message_end",
            message=_assistant_partial("the findings"),
        ),
    ]


async def _finish_task(app: ZenAgentApp, call_id: str = "c1", name: str = "research"):
    """Drive one complete parent task + child transcript through the controller."""
    chat = app.chat
    chat._on_event(
        AgentEvent(
            type="tool_execution_start",
            tool_call_id=call_id,
            tool_name="task",
            args={"prompt": "go", "name": name},
        )
    )
    chat.on_subagent_event(call_id, "start", {"name": name, "model": "m"})
    for event in _child_events():
        chat.on_subagent_event(call_id, "event", {"event": event})
    chat.on_subagent_event(
        call_id,
        "end",
        {"name": name, "status": "ok", "duration_ms": 1000, "cost": 0.01, "tokens": 9},
    )
    chat._on_event(
        AgentEvent(
            type="tool_execution_end",
            tool_call_id=call_id,
            tool_name="task",
            args={"prompt": "go", "name": name},
            result=ToolResultAgent(
                content=[TextContent(text="<task-result …>")],
                details={
                    "status": "ok",
                    "duration_ms": 1000,
                    "cost": 0.01,
                    "tokens": 9,
                },
            ),
            is_error=False,
            duration_ms=1000,
        )
    )


async def _open_view(app: ZenAgentApp, pilot, call_id: str = "c1") -> None:
    await pilot.press("ctrl+b")
    await pilot.pause()
    row = next(
        row
        for row in app.query_one(Sidebar).query(SidebarRow)
        if row.key == call_id
    )
    await pilot.click(row)
    await pilot.pause()


async def test_clicking_a_subagent_row_opens_the_read_only_view(fake_runtime):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await _finish_task(app)
        app._refresh_status()
        await pilot.pause()

        await _open_view(app, pilot)

        assert app._viewing == "c1"
        child = app.query_one("#subagent-transcript", TranscriptView)
        parent = app.query_one("#transcript", TranscriptView)
        assert child.display is True and parent.display is False
        assert child._count > 0, "the child transcript must render its messages"

        banner = app.query_one(SubagentBanner)
        assert banner.display is True
        assert "research" in banner.plain and "只读" in banner.plain

        prompt = app.query_one(PromptInput)
        assert prompt.disabled is True


async def test_escape_returns_to_the_parent_transcript(fake_runtime):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await _finish_task(app)
        app._refresh_status()
        await pilot.pause()
        await _open_view(app, pilot)

        await pilot.press("escape")
        await pilot.pause()

        assert app._viewing is None
        assert app.query_one("#transcript", TranscriptView).display is True
        assert app.query_one("#subagent-transcript", TranscriptView).display is False
        assert app.query_one(SubagentBanner).display is False
        prompt = app.query_one(PromptInput)
        assert prompt.disabled is False
        assert app.focused is prompt


async def test_the_parent_keeps_syncing_while_viewing(fake_runtime):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await _finish_task(app)
        app._refresh_status()
        await pilot.pause()
        await _open_view(app, pilot)
        before = app.query_one("#transcript", TranscriptView)._count

        app.chat._on_event(
            AgentEvent(type="message_start", message=UserMessage("queued work", 5))
        )
        app._refresh_status()
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

        parent = app.query_one("#transcript", TranscriptView)
        assert parent._count == before + 1
        assert parent.display is True


async def test_rows_without_a_transcript_are_not_clickable(fake_runtime):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        # A replayed task card with no sidecar: history only, nothing to open.
        app.chat._on_event(
            AgentEvent(
                type="tool_execution_start",
                tool_call_id="ghost",
                tool_name="task",
                args={"prompt": "go", "name": "ghost"},
            )
        )
        app.chat._on_event(
            AgentEvent(
                type="tool_execution_end",
                tool_call_id="ghost",
                tool_name="task",
                args={"prompt": "go", "name": "ghost"},
                result=ToolResultAgent(content=[TextContent(text="<task-result …>")]),
                is_error=False,
                duration_ms=1000,
            )
        )
        app._refresh_status()
        await pilot.press("ctrl+b")
        await pilot.pause()

        row = next(
            row for row in app.query_one(Sidebar).query(SidebarRow) if row.key == "ghost"
        )
        assert "ghost" in row.plain
        await pilot.click(row)
        await pilot.pause()

        assert app._viewing is None
        assert app.query_one("#subagent-transcript", TranscriptView).display is False


async def test_banner_flips_to_done_when_the_viewed_subagent_finishes(fake_runtime):
    """Spec §5.6: the view stays open and the banner says the child finished."""
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        chat = app.chat
        chat._on_event(
            AgentEvent(
                type="tool_execution_start",
                tool_call_id="c1",
                tool_name="task",
                args={"prompt": "go", "name": "research"},
            )
        )
        chat.on_subagent_event("c1", "start", {"name": "research", "model": "m"})
        for event in _child_events():
            chat.on_subagent_event("c1", "event", {"event": event})
        app._refresh_status()
        await pilot.pause()
        await _open_view(app, pilot)
        banner = app.query_one(SubagentBanner)
        assert "◐" in banner.plain

        chat.on_subagent_event(
            "c1",
            "end",
            {"name": "research", "status": "ok", "duration_ms": 1000, "cost": 0.01, "tokens": 9},
        )
        chat._on_event(
            AgentEvent(
                type="tool_execution_end",
                tool_call_id="c1",
                tool_name="task",
                args={"prompt": "go", "name": "research"},
                result=ToolResultAgent(
                    content=[TextContent(text="<task-result …>")],
                    details={
                        "status": "ok",
                        "duration_ms": 1000,
                        "cost": 0.01,
                        "tokens": 9,
                    },
                ),
                is_error=False,
                duration_ms=1000,
            )
        )
        app._refresh_status()
        await pilot.pause()

        assert app._viewing == "c1", "finishing must not kick the user out"
        assert "✓" in banner.plain
        assert "done" in banner.plain
