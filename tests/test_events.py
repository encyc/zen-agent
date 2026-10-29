"""Event protocol construction and AbortToken lifecycle tests."""

import asyncio

from pyagent.events import AbortToken, AgentEvent, AssistantEvent
from pyagent.types import AssistantMessage, TextContent, ToolCall, Usage

ASSISTANT_EVENT_TYPES = [
    "start",
    "text_start",
    "text_delta",
    "text_end",
    "thinking_start",
    "thinking_delta",
    "thinking_end",
    "toolcall_start",
    "toolcall_delta",
    "toolcall_end",
    "done",
    "error",
]

AGENT_EVENT_TYPES = [
    "agent_start",
    "agent_end",
    "turn_start",
    "turn_end",
    "message_start",
    "message_update",
    "message_end",
    "tool_execution_start",
    "tool_execution_end",
]


def _partial() -> AssistantMessage:
    """A live snapshot-so-far, as an adapter attaches to every event."""
    return AssistantMessage(
        content=[TextContent(text="He")],
        provider="zai",
        model="glm-5.3",
        usage=Usage(),
        stop_reason="pending",
        timestamp=1700000000,
    )


def test_assistant_event_constructs_for_every_type():
    for event_type in ASSISTANT_EVENT_TYPES:
        event = AssistantEvent(type=event_type, partial=_partial())
        assert event.type == event_type
        assert event.partial.stop_reason == "pending"
        assert event.content_index is None
        assert event.delta is None
        assert event.content is None
        assert event.tool_call is None
        assert event.reason is None


def test_assistant_event_carries_payload():
    event = AssistantEvent(
        type="toolcall_delta",
        partial=_partial(),
        content_index=1,
        delta='{"comm',
        tool_call=ToolCall(id="call_1", name="bash", arguments={}),
    )
    assert event.content_index == 1
    assert event.delta == '{"comm'
    assert event.tool_call == ToolCall(id="call_1", name="bash", arguments={})

    assert AssistantEvent(type="text_end", partial=_partial(), content="Hello").content == "Hello"
    assert AssistantEvent(type="done", partial=_partial(), reason="tool_use").reason == "tool_use"
    assert AssistantEvent(type="error", partial=_partial(), reason="error").reason == "error"


def test_agent_event_constructs_for_every_type():
    for event_type in AGENT_EVENT_TYPES:
        event = AgentEvent(type=event_type)
        assert event.type == event_type
        assert event.message is None
        assert event.assistant_event is None
        assert event.tool_call_id is None
        assert event.tool_name is None
        assert event.args is None
        assert event.result is None
        assert event.is_error is False
        assert event.duration_ms is None
        assert event.new_messages is None


def test_agent_event_carries_payload():
    inner = AssistantEvent(type="text_delta", partial=_partial(), delta="lo")
    message_update = AgentEvent(type="message_update", assistant_event=inner)
    assert message_update.assistant_event is inner

    tool_end = AgentEvent(
        type="tool_execution_end",
        tool_call_id="call_1",
        tool_name="bash",
        args={"command": "ls"},
        result="file1\nfile2",
        is_error=False,
        duration_ms=42,
    )
    assert tool_end.args == {"command": "ls"}
    assert tool_end.result == "file1\nfile2"
    assert tool_end.duration_ms == 42

    messages = [_partial()]
    assert AgentEvent(type="agent_end", new_messages=messages).new_messages == messages


async def test_abort_token_lifecycle():
    token = AbortToken()
    assert token.aborted is False
    token.abort()
    assert token.aborted is True
    # wait() on an already-aborted token must return immediately, not hang.
    await asyncio.wait_for(token.wait(), timeout=0.1)
