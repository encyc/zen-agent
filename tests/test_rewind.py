"""Checkpoint/rewind: markers, projection and safe failure modes."""

from zenagent.app.rewind import RewindState, build_checkpoint_tools
from zenagent.types import (
    AssistantMessage,
    TextContent,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
)


class FakeAgent:
    def __init__(self, messages=None) -> None:
        self.messages = list(messages or [])


def user(text: str) -> UserMessage:
    return UserMessage(content=text, timestamp=0)


def tool_result(call_id: str) -> ToolResultMessage:
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
    # the report lands at the END of the projection — the same position the
    # durable branch record gives it in the append-only file
    assert texts[2] == "next question"
    assert "bug is in parser.py" in texts[-1] and "find the bug" in texts[-1]
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


def test_report_message_matches_the_projection():
    state = RewindState()
    state.checkpoint(2, "goal text")
    state.arm(4, "learnings", "c1")

    message = state.report_message()
    projected = state.transform(
        [user("a"), user("b"), user("c"), user("d")]
    )

    assert message.content == "[rewind] goal: goal text\n\nlearnings"
    assert projected[2].content == message.content


def test_recorded_flag_resets_on_checkpoint_and_clear():
    state = RewindState()
    state.checkpoint(2, "g")
    state.arm(4, "r", "c1")
    state.recorded = True

    state.checkpoint(1, "g2")
    assert state.recorded is False

    state.arm(3, "r2", "c2")
    state.recorded = True
    state.clear()
    assert state.recorded is False


# -- durable branch projection: positions, pairs, and one report object ------


def _assistant_call(call_id: str, name: str) -> AssistantMessage:
    return AssistantMessage(
        content=[ToolCall(id=call_id, name=name, arguments={})],
        provider="test",
        model="m",
        usage=Usage(),
        stop_reason="tool_use",
        timestamp=0,
    )


def test_transform_keeps_the_checkpoint_round_results_in_the_head():
    # checkpoint() records its index before its own result lands: the head must
    # walk over that round's results or it ends on an unanswered tool call.
    state = RewindState()
    messages = [
        user("a"),
        _assistant_call("k1", "checkpoint"),
        tool_result("k1"),
        user("explore"),
        user("more"),
        _assistant_call("k2", "rewind"),
        tool_result("k2"),
    ]
    state.checkpoint(2, "goal")  # len(agent.messages) when checkpoint ran
    state.arm(6, "report", "k2")  # ... and when rewind ran

    projected = state.transform(messages)

    contents = [getattr(message, "content", "") for message in projected]
    assert contents[:3] == ["a", messages[1].content, messages[2].content]
    assert "explore" not in str(contents) and "more" not in str(contents)
    # the head's call is paired with its result; the rewind result is gone
    assert not [
        message
        for message in projected
        if isinstance(message, ToolResultMessage)
        and getattr(message, "tool_call_id", "") == "k2"
    ]


def test_transform_drops_sibling_call_results_from_the_abandoned_round():
    # a rewind batched with another tool: that sibling's result lands after the
    # rewind index, but its call is in the abandoned span — an orphan result.
    state = RewindState()
    sibling = AssistantMessage(
        content=[
            ToolCall(id="k2", name="rewind", arguments={}),
            ToolCall(id="k3", name="bash", arguments={}),
        ],
        provider="test",
        model="m",
        usage=Usage(),
        stop_reason="tool_use",
        timestamp=0,
    )
    messages = [
        user("a"),
        _assistant_call("k1", "checkpoint"),
        tool_result("k1"),
        user("explore"),
        sibling,
        tool_result("k3"),
        tool_result("k2"),
    ]
    state.checkpoint(2, "goal")
    state.arm(5, "report", "k2")  # the sibling assistant turn is the boundary

    projected = state.transform(messages)

    survivors = {
        getattr(message, "tool_call_id", None)
        for message in projected
        if isinstance(message, ToolResultMessage)
    }
    assert survivors == {"k1"}, "only the kept head's pairing may survive"


def test_transform_uses_the_supplied_report_object():
    state = RewindState()
    state.checkpoint(1, "g")
    state.arm(2, "r", "c1")
    supplied = state.report_message()

    projected = state.transform([user("a"), user("b")], report=supplied)

    assert projected[-1] is supplied
