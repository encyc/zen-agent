"""Resume integration: a subagent transcript written in one session is
viewable after the app and runtime are rebuilt from the session file.

This is the end-to-end proof for the persistence slice: the child runs through
the real ``task`` tool with a fake stream, the sidecar lands next to the
session, and a fresh runtime resuming that session rebuilds the sidebar entry.
"""

from zenagent.cli.tui.app import ZenAgentApp
from zenagent.cli.tui.widgets.sidebar import Sidebar, SidebarRow
from zenagent.cli.tui.widgets.subagent_banner import SubagentBanner
from zenagent.cli.tui.widgets.transcript import TranscriptView
from zenagent.events import AssistantEvent
from zenagent.types import (
    AssistantMessage,
    TextContent,
    ToolCall,
    ToolResultMessage,
    Usage,
)


class TaskCallingStream:
    """Parent asks for one ``task`` call; the child answers with plain text."""

    async def __call__(self, model, messages, tools, options):
        names = {tool.name for tool in tools}
        partial = AssistantMessage(
            content=[],
            provider="p",
            model="m",
            usage=Usage(),
            stop_reason="pending",
            timestamp=0,
        )
        yield AssistantEvent(type="start", partial=partial)
        if "task" in names and not isinstance(messages[-1], ToolResultMessage):
            call = ToolCall(
                id="tc_task", name="task", arguments={"prompt": "go", "name": "research"}
            )
            partial.content = [call]
            yield AssistantEvent(
                type="toolcall_start", partial=partial, content_index=0, tool_call=call
            )
            yield AssistantEvent(
                type="toolcall_end", partial=partial, content_index=0, tool_call=call
            )
            partial.stop_reason = "tool_use"
            yield AssistantEvent(type="done", partial=partial, reason="tool_use")
            return
        partial.content = [TextContent("child answer")]
        partial.stop_reason = "stop"
        yield AssistantEvent(
            type="text_delta", partial=partial, content_index=0, delta="child answer"
        )
        yield AssistantEvent(type="done", partial=partial, reason="stop")


async def test_resumed_session_can_open_the_subagent_transcript(runtime_factory):
    session_path = None
    runtime = await runtime_factory(stream_fn=TaskCallingStream())
    app = ZenAgentApp(runtime)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await app.chat.submit("delegate this")
        await app.chat.wait_for_idle()
        app._refresh_status()
        await pilot.pause()
        session_path = runtime.session.path

        [entry] = app.chat.status.subagents
        assert entry.has_transcript is True

    # A fresh runtime resumes the session file; the app rebuilds from disk.
    resumed = await runtime_factory(stream_fn=TaskCallingStream(), resume=session_path)
    app2 = ZenAgentApp(resumed)
    async with app2.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        app2._refresh_status()
        await pilot.press("ctrl+b")
        await pilot.pause()

        [entry] = app2.chat.status.subagents
        assert (entry.call_id, entry.status) == ("tc_task", "ok")
        assert entry.has_transcript is True

        row = next(
            row
            for row in app2.query_one(Sidebar).query(SidebarRow)
            if row.key == "tc_task"
        )
        await pilot.click(row)
        await pilot.pause()

        assert app2._viewing == "tc_task"
        child = app2.query_one("#subagent-transcript", TranscriptView)
        assert child.display is True and child._count > 0
        banner = app2.query_one(SubagentBanner)
        assert "research" in banner.plain


async def test_interrupted_transcript_is_still_readable(runtime_factory):
    from zenagent.agent.subagents import SubagentStore, task_dir

    runtime = await runtime_factory(stream_fn=TaskCallingStream())
    app = ZenAgentApp(runtime)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await app.chat.submit("delegate this")
        await app.chat.wait_for_idle()
        session_path = runtime.session.path

    # Simulate a crash mid-run: the file has messages but no `end` line.
    sidecar = task_dir(session_path) / "tc_task.jsonl"
    lines = [
        line for line in sidecar.read_text().splitlines() if '"type": "end"' not in line
    ]
    sidecar.write_text("\n".join(lines) + "\n")

    resumed = await runtime_factory(stream_fn=TaskCallingStream(), resume=session_path)
    app2 = ZenAgentApp(resumed)
    async with app2.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        app2._refresh_status()

        [entry] = app2.chat.status.subagents
        assert entry.status == "interrupted"
        assert entry.has_transcript is True
        views = app2.chat.subagent_transcript("tc_task")
        assert views
