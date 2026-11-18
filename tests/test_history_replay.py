"""Replay-safe transcript projection: the view a provider is allowed to see.

The durable transcript keeps failed and aborted turns so the UI and ``--resume``
can show what happened; ``replayable`` is the wire-facing projection that drops
what no provider accepts — an assistant turn with neither text nor tool calls
(DeepSeek answers ``400 Invalid assistant message: content or tool_calls must
be set``), partial thinking from a turn that never finished, and tool calls
whose results are missing.
"""

from zenagent.providers.history import (
    downgrade_unsupported_images,
    replayable,
    split_error_tool_result_images,
)
from zenagent.types import (
    AssistantMessage,
    ImageContent,
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


# ---------------------------------------------------------------------------
# Images: unsupported-model downgrade and Anthropic error-result split
# ---------------------------------------------------------------------------


def _image(data: str = "aW1n") -> ImageContent:
    return ImageContent(data=data, mime_type="image/png")


def _image_result(call_id: str, name: str, image: str, *, text: str = "") -> ToolResultMessage:
    content = [TextContent(text=text)] if text else []
    content.append(ImageContent(data=image, mime_type="image/png"))
    return ToolResultMessage(
        tool_call_id=call_id,
        tool_name=name,
        content=content,
        is_error=False,
        timestamp=2,
    )


def test_downgrade_leaves_a_vision_transcript_untouched():
    messages = [_user("look"), _image_result("c1", "shot", "aW1n")]
    assert downgrade_unsupported_images(messages, supports_vision=True) is messages


def test_downgrade_replaces_images_with_explicit_placeholders():
    first, second, third = _image("aW1nMQ=="), _image("aW1nMg=="), _image("aW1nMw==")
    user = UserMessage(
        content=[TextContent(text="look"), first, second, TextContent(text="now")],
        timestamp=1,
    )
    result = ToolResultMessage(
        tool_call_id="c1",
        tool_name="shot",
        content=[third, TextContent(text="done")],
        is_error=False,
        timestamp=2,
    )

    downgraded = downgrade_unsupported_images(
        [user, result], supports_vision=False
    )

    assert downgraded[0].content == [
        TextContent(text="look"),
        TextContent(text="(image omitted: model does not support images)"),
        TextContent(text="now"),
    ]
    assert downgraded[1].content == [
        TextContent(text="(tool image omitted: model does not support images)"),
        TextContent(text="done"),
    ]
    # The stored transcript keeps its images.
    assert user.content[1] is first and result.content[0] is third


def test_downgrade_keeps_image_free_messages_verbatim():
    messages = [_user("hi"), _assistant([TextContent(text="done")])]
    downgraded = downgrade_unsupported_images(messages, supports_vision=False)
    assert downgraded == messages
    assert all(copy is message for copy, message in zip(downgraded, messages))


def test_error_tool_result_images_move_after_the_result():
    image = _image()
    message = ToolResultMessage(
        tool_call_id="c1",
        tool_name="shot",
        content=[TextContent(text="failed"), image, TextContent(text="tail")],
        is_error=True,
        timestamp=2,
    )

    split = split_error_tool_result_images(message)

    assert split is not None
    assert split.content == [
        TextContent(text="failed"),
        TextContent(text="(image attached after the tool results)"),
        TextContent(text="tail"),
    ]
    assert split.caption == "Images from the error result of tool call c1 (shot):"
    assert split.images == [image]
    assert message.content[1] is image


def test_only_error_results_with_images_are_split():
    assert split_error_tool_result_images(_result("c1")) is None
    assert split_error_tool_result_images(_image_result("c2", "shot", "aW1n")) is None


def test_empty_image_data_becomes_a_placeholder():
    """A hydrated image whose blob file was gone arrives with empty data; the
    wire view must replace it with text — an empty base64 payload is a 400."""
    empty_image = ImageContent(data="", mime_type="image/png")
    messages = [
        _user("look"),
        _assistant([ToolCall(id="c1", name="shot", arguments={})], stop_reason="tool_use"),
        ToolResultMessage(
            tool_call_id="c1",
            tool_name="shot",
            content=[TextContent(text="shot:"), empty_image],
            is_error=False,
            timestamp=3,
        ),
        _assistant([TextContent(text="ok")]),
    ]

    replayed = replayable(messages)

    result = replayed[2]
    assert all(not isinstance(block, ImageContent) for block in result.content)
    assert any(
        "(image omitted" in block.text
        for block in result.content
        if isinstance(block, TextContent)
    )


def test_empty_image_in_a_user_turn_becomes_a_placeholder():
    empty_image = ImageContent(data="", mime_type="image/png")
    messages = [
        UserMessage(content=[TextContent(text="see:"), empty_image], timestamp=1),
    ]

    replayed = replayable(messages)

    content = replayed[0].content
    assert all(not isinstance(block, ImageContent) for block in content)
    assert any("(image omitted" in b.text for b in content if isinstance(b, TextContent))


def test_real_images_pass_through_unchanged():
    image = ImageContent(data="aW1n", mime_type="image/png")
    messages = [
        UserMessage(content=[image], timestamp=1),
        _assistant([TextContent(text="ok")]),
    ]

    replayed = replayable(messages)

    assert replayed[0].content[0] is image
