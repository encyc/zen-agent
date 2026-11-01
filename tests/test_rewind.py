"""Checkpoint/rewind: markers, projection and safe failure modes."""

from zenagent.app.rewind import RewindState, build_checkpoint_tools
from zenagent.types import ToolResultMessage, UserMessage


class FakeAgent:
    def __init__(self, messages=None) -> None:
        self.messages = list(messages or [])


def user(text: str) -> UserMessage:
    return UserMessage(content=text, timestamp=0)


def tool_result(call_id: str) -> ToolResultMessage:
    from zenagent.types import TextContent

    return ToolResultMessage(
        tool_call_id=call_id,
        tool_name="rewind",
        content=[TextContent(text="rewind armed")],
        is_error=False,
        timestamp=0,
    )


def test_transform_is_identity_without_a_rewind():
    state = RewindState()
    messages = [user("system"), user("hi")]
    assert state.transform(messages) is messages


def test_rewind_projects_head_report_and_tail():
    state = RewindState()
    agent = FakeAgent([user("system"), user("task")])
    checkpoint, rewind = build_checkpoint_tools(lambda: agent, state)
    # checkpoint at index 2
    import asyncio

    asyncio.run(checkpoint.execute("c1", {"goal": "find the bug"}, None))
    # the exploration appends two turns
    agent.messages.extend([user("read a"), user("read b")])
    asyncio.run(rewind.execute("c2", {"report": "bug is in parser.py"}, None))

    messages = [*agent.messages, tool_result("c2"), user("next question")]
    projected = state.transform(messages)
    texts = [getattr(message, "content", "") for message in projected]
    assert texts[0] == "system"
    assert texts[1] == "task"
    assert "bug is in parser.py" in texts[2] and "find the bug" in texts[2]
    assert texts[-1] == "next question"
    assert "read a" not in texts and "read b" not in texts
    assert tool_result("c2") not in projected


def test_rewind_without_checkpoint_is_an_error():
    state = RewindState()
    agent = FakeAgent()
    _, rewind = build_checkpoint_tools(lambda: agent, state)

    import asyncio

    result = asyncio.run(rewind.execute("c1", {"report": "x"}, None))
    assert result.is_error is True
    assert "no checkpoint" in result.content[0].text


def test_rewind_with_an_empty_report_is_an_error():
    state = RewindState()
    agent = FakeAgent()
    checkpoint, rewind = build_checkpoint_tools(lambda: agent, state)
    import asyncio

    asyncio.run(checkpoint.execute("c1", {"goal": "g"}, None))
    result = asyncio.run(rewind.execute("c2", {"report": "  "}, None))
    assert result.is_error is True
    assert "non-empty report" in result.content[0].text


def test_checkpoint_needs_a_goal():
    state = RewindState()
    checkpoint, _ = build_checkpoint_tools(lambda: FakeAgent(), state)
    import asyncio

    result = asyncio.run(checkpoint.execute("c1", {"goal": ""}, None))
    assert result.is_error is True
    assert "goal" in result.content[0].text


def test_stale_indexes_drop_the_projection_instead_of_corrupting():
    state = RewindState()
    state.checkpoint(5, "goal")
    state.arm(8, "report", "c1")
    # transcript shrank (undo/compaction) — projection must bail out safely
    messages = [user("system")]
    assert state.transform(messages) is messages
    assert state.armed is False


async def test_runtime_registers_checkpoint_and_rewind(runtime_factory):
    runtime = await runtime_factory()
    names = [tool.name for tool in runtime.agent.tools]
    assert "checkpoint" in names and "rewind" in names


async def test_runtime_transform_applies_the_rewind(runtime_factory):
    runtime = await runtime_factory()
    messages = [user("system"), user("task")]
    runtime.agent.replace_messages(messages)
    runtime.rewind.checkpoint(2, "goal")
    runtime.rewind.arm(2, "report", "c1")
    projected = await runtime._transform_context(messages)
    assert any("report" in str(getattr(m, "content", "")) for m in projected)
    assert len(projected) == 3  # system + task + report


async def test_session_change_clears_rewind(runtime_factory):
    runtime = await runtime_factory()
    runtime.rewind.checkpoint(1, "goal")
    runtime.rewind.arm(1, "report", "c1")
    runtime.session = runtime.session
    assert runtime.rewind.armed is False
    assert runtime.rewind.checkpoint_index is None
