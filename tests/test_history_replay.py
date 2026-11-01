"""Replay-safe transcript projection: the view a provider is allowed to see.

The durable transcript keeps failed and aborted turns so the UI and ``--resume``
can show what happened; ``replayable`` is the wire-facing projection that drops
what no provider accepts — an assistant turn with neither text nor tool calls
(DeepSeek answers ``400 Invalid assistant message: content or tool_calls must
be set``), partial thinking from a turn that never finished, and tool calls
whose results are missing.
"""

from zenagent.providers.history import replayable
from zenagent.types import (
    AssistantMessage,
    SystemMessage,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
)


def _assistant(blocks, stop_reason="stop", **kwargs) -> AssistantMessage:
    return AssistantMessage(
        content=blocks,
        provider="test",
        model="test-model",
        usage=Usage(),
        stop_reason=stop_reason,
        timestamp=1,
        **kwargs,
    )


def _user(text: str) -> UserMessage:
    return UserMessage(content=text, timestamp=1)


def _result(call_id: str) -> ToolResultMessage:
    return ToolResultMessage(
        tool_call_id=call_id,
        tool_name="noop",
        content=[TextContent(text="ok")],
        is_error=False,
        timestamp=2,
    )


def _call(call_id: str) -> ToolCall:
    return ToolCall(id=call_id, name="noop", arguments={})


# ---------------------------------------------------------------------------
# Healthy transcripts pass through untouched
# ---------------------------------------------------------------------------


def test_healthy_transcript_keeps_every_message_and_its_identity():
    messages = [
        SystemMessage(content="sys", timestamp=0),
        _user("hi"),
        _assistant([TextContent(text="checking"), _call("c1")], stop_reason="tool_use"),
        _result("c1"),
        _assistant([TextContent(text="done")]),
    ]
    kept = replayable(messages)
    assert kept == messages
    assert all(kept_message is message for kept_message, message in zip(kept, messages))


def test_completed_thinking_only_turn_is_replayed():
    turn = _assistant([ThinkingContent(thinking="reasoned")])
    messages = [_user("hi"), turn]
    assert replayable(messages)[1] is turn


# ---------------------------------------------------------------------------
# Failed / aborted turns
# ---------------------------------------------------------------------------


def test_aborted_thinking_only_turn_is_dropped():
    """The exact shape that poisoned a real session: abort mid-thinking."""
    aborted = _assistant([ThinkingContent(thinking="partial")], stop_reason="aborted")
    messages = [_user("hi"), aborted, _user("again")]
    assert replayable(messages) == [messages[0], messages[2]]


def test_error_turn_with_empty_content_is_dropped():
    failed = _assistant(
        [], stop_reason="error", error_message="HTTPStatusError: 400 Bad Request"
    )
    messages = [_user("hi"), failed, _user("again")]
    assert replayable(messages) == [messages[0], messages[2]]


def test_pending_turn_is_treated_as_failed():
    pending = _assistant([ThinkingContent(thinking="x"), _call("c1")], stop_reason="pending")
    assert replayable([_user("hi"), pending]) == [_user("hi")]


def test_failed_turn_keeps_its_text_but_drops_thinking_and_calls():
    aborted = _assistant(
        [
            ThinkingContent(thinking="hmm"),
            TextContent(text="partial answer"),
            _call("c1"),
        ],
        stop_reason="aborted",
    )
    kept = replayable([_user("hi"), aborted])
    assert kept[1] is not aborted  # projected copy, transcript untouched
    assert kept[1].content == [TextContent(text="partial answer")]
    assert len(aborted.content) == 3  # original still has everything


# ---------------------------------------------------------------------------
# Tool-call pairing
# ---------------------------------------------------------------------------


def test_unanswered_tool_call_is_dropped_and_answered_ones_survive():
    assistant = _assistant(
        [TextContent(text="checking"), _call("c1"), _call("c2")],
        stop_reason="tool_use",
    )
    result = _result("c1")
    kept = replayable([_user("hi"), assistant, result, _assistant([TextContent(text="done")])])
    assert kept[1] is not assistant
    assert kept[1].content == [TextContent(text="checking"), _call("c1")]
    assert kept[2] is result


def test_orphan_tool_result_is_dropped():
    messages = [_user("hi"), _result("ghost"), _assistant([TextContent(text="done")])]
    assert replayable(messages) == [messages[0], messages[2]]


def test_result_before_its_call_does_not_count_as_answered():
    """Corrupt ordering must not resurrect a dangling call."""
    messages = [_result("c1"), _assistant([_call("c1")], stop_reason="tool_use")]
    assert replayable(messages) == []
