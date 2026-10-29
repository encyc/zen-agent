"""Round-trip and error-handling tests for the unified data model."""

import pytest

from pyagent.types import (
    AssistantMessage,
    Cost,
    ImageContent,
    SystemMessage,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolDef,
    ToolResultMessage,
    Usage,
    UserMessage,
    message_from_json,
)


def _assistant(content, **overrides):
    """Build an AssistantMessage with defaults, overriding a few fields."""
    kwargs = {
        "provider": "zai",
        "model": "glm-5.3",
        "usage": Usage(),
        "stop_reason": "stop",
        "timestamp": 1700000000,
    }
    kwargs.update(overrides)
    return AssistantMessage(content=content, **kwargs)


def test_system_message_round_trip():
    msg = SystemMessage(content="You are a coding agent.", timestamp=1000)
    assert msg.to_json() == {
        "role": "system",
        "content": "You are a coding agent.",
        "timestamp": 1000,
    }
    assert message_from_json(msg.to_json()) == msg


def test_user_message_round_trip_plain_string():
    msg = UserMessage(content="hello", timestamp=1234)
    assert msg.to_json() == {"role": "user", "content": "hello", "timestamp": 1234}
    assert message_from_json(msg.to_json()) == msg


def test_user_message_round_trip_with_blocks():
    msg = UserMessage(
        content=[
            TextContent(text="look at this"),
            ImageContent(data="aGVsbG8=", mime_type="image/png"),
        ],
        timestamp=1234,
    )
    round_tripped = message_from_json(msg.to_json())
    assert round_tripped == msg
    assert isinstance(round_tripped.content, list)
    assert round_tripped.content[1].mime_type == "image/png"


def test_assistant_message_round_trip_full():
    usage = Usage(
        input=10,
        output=20,
        cache_read=3,
        cache_write=2,
        reasoning=7,
        cost=Cost(input=0.001, output=0.002, total=0.003),
    )
    msg = _assistant(
        [
            TextContent(text="Let me check."),
            ThinkingContent(thinking="considering options"),
            ToolCall(id="call_1", name="bash", arguments={"command": "ls"}),
        ],
        usage=usage,
        stop_reason="tool_use",
        response_id="resp_9",
        duration_ms=1500,
    )
    data = msg.to_json()
    assert data["role"] == "assistant"
    assert data["usage"] == {
        "input": 10,
        "output": 20,
        "cache_read": 3,
        "cache_write": 2,
        "reasoning": 7,
        "cost": {
            "input": 0.001,
            "output": 0.002,
            "cache_read": 0.0,
            "cache_write": 0.0,
            "total": 0.003,
        },
    }
    round_tripped = message_from_json(data)
    assert round_tripped == msg
    assert round_tripped.usage == usage
    assert round_tripped.content[2].arguments == {"command": "ls"}
    assert round_tripped.response_id == "resp_9"
    assert round_tripped.duration_ms == 1500


def test_assistant_message_round_trip_minimal_defaults():
    msg = _assistant(
        [TextContent(text="hi"), ThinkingContent(thinking="hmm"), ToolCall("c", "bash", {})]
    )
    assert msg.error_message is None
    assert msg.usage.cost == Cost()
    round_tripped = message_from_json(msg.to_json())
    assert round_tripped == msg
    assert round_tripped.usage == Usage()
    assert round_tripped.content[2].arguments_error is None


def test_assistant_message_round_trip_with_error():
    msg = _assistant(
        [TextContent(text="partial")],
        stop_reason="error",
        error_message="boom",
    )
    round_tripped = message_from_json(msg.to_json())
    assert round_tripped == msg
    assert round_tripped.error_message == "boom"


def test_thinking_content_preserves_signature_and_redacted():
    msg = _assistant(
        [ThinkingContent(thinking="redacted blob", signature="sig-1", redacted=True)]
    )
    round_tripped = message_from_json(msg.to_json())
    block = round_tripped.content[0]
    assert block == ThinkingContent(
        thinking="redacted blob", signature="sig-1", redacted=True
    )
    assert block.signature == "sig-1"
    assert block.redacted is True


def test_tool_call_preserves_arguments_error():
    msg = _assistant(
        [ToolCall(id="call_2", name="bash", arguments={}, arguments_error="invalid JSON")]
    )
    round_tripped = message_from_json(msg.to_json())
    assert round_tripped.content[0].arguments_error == "invalid JSON"
    assert round_tripped.content[0] == ToolCall(
        id="call_2", name="bash", arguments={}, arguments_error="invalid JSON"
    )


def test_tool_result_message_round_trip():
    msg = ToolResultMessage(
        tool_call_id="call_1",
        tool_name="bash",
        content=[
            TextContent(text="file1\nfile2"),
            ImageContent(data="aGk=", mime_type="image/jpeg"),
        ],
        is_error=False,
        timestamp=99,
    )
    data = msg.to_json()
    assert data["role"] == "toolResult"
    assert message_from_json(data) == msg
    err = ToolResultMessage(
        tool_call_id="call_2",
        tool_name="bash",
        content=[TextContent(text="boom")],
        is_error=True,
        timestamp=100,
    )
    assert message_from_json(err.to_json()) == err


def test_content_block_type_tags():
    assert TextContent("hi").to_json() == {"type": "text", "text": "hi"}
    assert ThinkingContent("h").to_json() == {
        "type": "thinking",
        "thinking": "h",
        "signature": None,
        "redacted": False,
    }
    assert ImageContent("aGk=", "image/png").to_json() == {
        "type": "image",
        "data": "aGk=",
        "mime_type": "image/png",
    }
    assert ToolCall("id", "bash", {"x": 1}).to_json() == {
        "type": "toolCall",
        "id": "id",
        "name": "bash",
        "arguments": {"x": 1},
        "arguments_error": None,
    }


def test_tool_def_to_json():
    tool = ToolDef(
        name="bash",
        description="Run a shell command",
        parameters={"type": "object", "properties": {}},
    )
    assert tool.to_json() == {
        "name": "bash",
        "description": "Run a shell command",
        "parameters": {"type": "object", "properties": {}},
    }


def test_message_from_json_unknown_role_raises():
    with pytest.raises(ValueError, match="unknown message role"):
        message_from_json({"role": "wizard", "content": "x", "timestamp": 1})


def test_message_from_json_missing_role_raises():
    with pytest.raises(ValueError, match="role"):
        message_from_json({"content": "x"})


def test_message_from_json_missing_required_key_raises():
    data = {"role": "assistant", "content": [], "usage": {}}
    with pytest.raises(ValueError, match="provider"):
        message_from_json(data)


def test_message_from_json_unknown_block_type_raises():
    data = {
        "role": "assistant",
        "content": [{"type": "video", "video": "…"}],
        "provider": "zai",
        "model": "glm-5.3",
        "usage": {},
        "stop_reason": "stop",
        "timestamp": 1,
    }
    with pytest.raises(ValueError, match="unknown content block type"):
        message_from_json(data)


def test_cost_round_trips_cache_components():
    """Cached input is priced separately and must survive serialization."""
    usage = Usage(
        input=100,
        output=50,
        cache_read=900,
        cache_write=20,
        cost=Cost(input=0.1, output=0.2, cache_read=0.03, cache_write=0.025, total=0.355),
    )
    message = _assistant([TextContent(text="x")], usage=usage)
    data = message.to_json()
    assert data["usage"]["cost"] == {
        "input": 0.1,
        "output": 0.2,
        "cache_read": 0.03,
        "cache_write": 0.025,
        "total": 0.355,
    }
    assert message_from_json(data) == message


def test_cost_defaults_to_zero_cache_components():
    assert Cost().to_json() == {
        "input": 0.0,
        "output": 0.0,
        "cache_read": 0.0,
        "cache_write": 0.0,
        "total": 0.0,
    }
