"""Tests for the request side of the OpenAI-compatible adapter.

Covers ``to_request_messages`` (unified message model → OpenAI message
JSON, incl. the deepseek ``reasoning_content`` backfill) and
``build_params`` (per-dialect ``thinking`` blocks, ``max_tokens_field``,
``stream_options``, tool schema, and the zai ``tool_stream`` quirk).
"""

from zenagent.providers.catalog import Model, find_model, load_catalog
from zenagent.providers.compat import CompatFlags
from zenagent.providers.openai_completions import (
    StreamOptions,
    build_params,
    to_request_messages,
)
from zenagent.types import (
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
)

# Compat blocks mirroring the catalog's zai / deepseek dialect entries
# (see zenagent/catalog.yaml: zai-coding-cn/glm-5.3, deepseek/deepseek-flash).
ZAI = CompatFlags(
    thinking_format="zai",
    zai_tool_stream=True,
    supports_developer_role=False,
    supports_reasoning_effort=True,
)
DEEPSEEK = CompatFlags(
    thinking_format="deepseek",
    requires_reasoning_content_on_assistant=True,
    supports_developer_role=False,
)

WEATHER_SCHEMA = {
    "type": "object",
    "properties": {"city": {"type": "string"}},
    "required": ["city"],
}
WEATHER = ToolDef(
    name="get_weather",
    description="Get current weather for a city",
    parameters=WEATHER_SCHEMA,
)


def _model(compat: CompatFlags, *, reasoning: bool = True, max_tokens: int = 131072):
    return Model(
        id="test-model",
        provider="test",
        api="openai-completions",
        base_url="https://api.test/v1",
        context_window=1000000,
        max_tokens=max_tokens,
        reasoning=reasoning,
        cost=Cost(input=0.0, output=0.0),
        compat=compat,
    )


def _assistant(blocks) -> AssistantMessage:
    return AssistantMessage(
        content=blocks,
        provider="test",
        model="test-model",
        usage=Usage(),
        stop_reason="stop",
        timestamp=1,
    )


# --------------------------------------------------------------- StreamOptions


def test_stream_options_defaults():
    options = StreamOptions()
    assert options.api_key == ""
    assert options.thinking is None
    assert options.max_tokens is None
    assert options.temperature is None
    assert options.session_id is None
    assert options.extra_headers is None
    assert options.abort is None


# ------------------------------------------------------- to_request_messages


def test_system_and_string_user_map_verbatim():
    messages = [
        SystemMessage(content="You are a coding agent.", timestamp=1),
        UserMessage(content="hello", timestamp=2),
    ]
    assert to_request_messages(messages, ZAI, supports_vision=False) == [
        {"role": "system", "content": "You are a coding agent."},
        {"role": "user", "content": "hello"},
    ]


def test_user_blocks_map_to_openai_content_parts():
    message = UserMessage(
        content=[
            TextContent(text="what is in this?"),
            ImageContent(data="aGVsbG8=", mime_type="image/png"),
        ],
        timestamp=1,
    )
    assert to_request_messages([message], ZAI, supports_vision=True) == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "what is in this?"},
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64,aGVsbG8="},
                },
            ],
        }
    ]


def test_user_images_become_a_placeholder_without_vision():
    message = UserMessage(
        content=[
            TextContent(text="what is in this?"),
            ImageContent(data="aGVsbG8=", mime_type="image/png"),
        ],
        timestamp=1,
    )
    assert to_request_messages([message], ZAI, supports_vision=False) == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "what is in this?"},
                {
                    "type": "text",
                    "text": "(image omitted: model does not support images)",
                },
            ],
        }
    ]


def test_assistant_multi_block_joins_text_and_builds_tool_calls():
    message = _assistant(
        [
            TextContent(text="Let me check."),
            TextContent(text="One moment."),
            ThinkingContent(thinking="the user asked about SF"),
            ToolCall(id="call_1", name="get_weather", arguments={"city": "SF"}),
        ]
    )
    assert to_request_messages([message], ZAI, supports_vision=False) == [
        {
            "role": "assistant",
            "content": "Let me check.\nOne moment.",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "arguments": '{"city": "SF"}',
                    },
                }
            ],
        }
    ]


def test_assistant_reasoning_content_backfilled_only_for_deepseek():
    message = _assistant(
        [
            ThinkingContent(thinking="the user asked about SF"),
            ToolCall(id="call_1", name="get_weather", arguments={"city": "SF"}),
        ]
    )
    deepseek = to_request_messages([message], DEEPSEEK, supports_vision=False)[0]
    assert deepseek["reasoning_content"] == "the user asked about SF"
    zai = to_request_messages([message], ZAI, supports_vision=False)[0]
    assert "reasoning_content" not in zai


def test_assistant_text_only_has_no_tool_calls_or_reasoning_content():
    message = _assistant([TextContent(text="done")])
    for compat in (ZAI, DEEPSEEK):
        assert to_request_messages([message], compat, supports_vision=False) == [
            {"role": "assistant", "content": "done"}
        ]


def test_assistant_tool_call_only_content_is_none():
    message = _assistant([ToolCall(id="call_1", name="get_weather", arguments={})])
    request = to_request_messages([message], DEEPSEEK, supports_vision=False)[0]
    assert request["content"] is None
    assert request["tool_calls"][0]["function"]["arguments"] == "{}"


def test_assistant_thinking_only_sends_empty_content_not_null():
    """DeepSeek rejects ``content: null`` without tool_calls (400)."""
    message = _assistant([ThinkingContent(thinking="pondering")])
    request = to_request_messages([message], DEEPSEEK, supports_vision=False)[0]
    assert request == {
        "role": "assistant",
        "content": "",
        "reasoning_content": "pondering",
    }


def test_assistant_without_blocks_sends_empty_content_not_null():
    request = to_request_messages([_assistant([])], ZAI, supports_vision=False)[0]
    assert request == {"role": "assistant", "content": ""}


def test_tool_call_with_arguments_error_serializes_empty_json():
    message = _assistant(
        [
            ToolCall(
                id="call_2",
                name="broken",
                arguments={"partial": "junk"},
                arguments_error="invalid JSON at position 3",
            )
        ]
    )
    request = to_request_messages([message], ZAI, supports_vision=False)[0]
    assert request["tool_calls"][0]["function"]["arguments"] == "{}"


def test_tool_result_text_only_maps_to_tool_role():
    message = ToolResultMessage(
        tool_call_id="call_1",
        tool_name="get_weather",
        content=[
            TextContent(text="72F and sunny"),
            TextContent(text="wind 5mph"),
        ],
        is_error=False,
        timestamp=1,
    )
    assert to_request_messages([message], ZAI, supports_vision=False) == [
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "content": "72F and sunny\nwind 5mph",
        }
    ]


def test_tool_result_images_become_a_placeholder_without_vision():
    """A text-only model is told the image exists instead of silently losing it."""
    message = ToolResultMessage(
        tool_call_id="call_1",
        tool_name="get_weather",
        content=[
            TextContent(text="72F and sunny"),
            ImageContent(data="aGVsbG8=", mime_type="image/png"),
            TextContent(text="wind 5mph"),
        ],
        is_error=False,
        timestamp=1,
    )
    assert to_request_messages([message], ZAI, supports_vision=False) == [
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "content": (
                "72F and sunny\n"
                "(tool image omitted: model does not support images)\n"
                "wind 5mph"
            ),
        }
    ]


def test_tool_result_image_rides_in_a_user_turn_for_vision_models():
    """OpenAI tool messages are text-only, so the image follows as a user turn."""
    message = ToolResultMessage(
        tool_call_id="call_1",
        tool_name="get_weather",
        content=[
            TextContent(text="72F and sunny"),
            ImageContent(data="aGVsbG8=", mime_type="image/png"),
        ],
        is_error=False,
        timestamp=1,
    )
    assert to_request_messages([message], DEEPSEEK, supports_vision=True) == [
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "content": "72F and sunny",
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "Image returned by tool get_weather (call call_1):",
                },
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64,aGVsbG8="},
                },
            ],
        },
    ]


def test_image_only_tool_result_says_the_image_is_attached():
    message = ToolResultMessage(
        tool_call_id="call_1",
        tool_name="screenshot",
        content=[ImageContent(data="aGVsbG8=", mime_type="image/png")],
        is_error=False,
        timestamp=1,
    )
    assert to_request_messages([message], DEEPSEEK, supports_vision=True) == [
        {"role": "tool", "tool_call_id": "call_1", "content": "(see attached image)"},
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "Image returned by tool screenshot (call call_1):",
                },
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64,aGVsbG8="},
                },
            ],
        },
    ]


def test_parallel_tool_result_images_share_one_following_user_turn():
    """A user turn between two tool messages would break the tool-call batch."""
    results = [
        ToolResultMessage(
            tool_call_id="call_1",
            tool_name="screenshot",
            content=[ImageContent(data="aW1nMQ==", mime_type="image/png")],
            is_error=False,
            timestamp=2,
        ),
        ToolResultMessage(
            tool_call_id="call_2",
            tool_name="read_page",
            content=[TextContent(text="all clear")],
            is_error=False,
            timestamp=3,
        ),
        ToolResultMessage(
            tool_call_id="call_3",
            tool_name="crop",
            content=[
                ImageContent(data="aW1nMg==", mime_type="image/jpeg"),
                ImageContent(data="aW1nMw==", mime_type="image/jpeg"),
            ],
            is_error=False,
            timestamp=4,
        ),
    ]
    assert to_request_messages(results, DEEPSEEK, supports_vision=True) == [
        {"role": "tool", "tool_call_id": "call_1", "content": "(see attached image)"},
        {"role": "tool", "tool_call_id": "call_2", "content": "all clear"},
        {"role": "tool", "tool_call_id": "call_3", "content": "(see attached image)"},
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "Image returned by tool screenshot (call call_1):",
                },
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64,aW1nMQ=="},
                },
                {
                    "type": "text",
                    "text": "2 images returned by tool crop (call call_3):",
                },
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/jpeg;base64,aW1nMg=="},
                },
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/jpeg;base64,aW1nMw=="},
                },
            ],
        },
    ]


def test_empty_and_whitespace_tool_output_gets_a_placeholder():
    for text in ("", "\r\n", " \n\t"):
        message = ToolResultMessage(
            tool_call_id="call_1",
            tool_name="bash",
            content=[TextContent(text=text)],
            is_error=False,
            timestamp=1,
        )
        assert to_request_messages([message], ZAI, supports_vision=False) == [
            {"role": "tool", "tool_call_id": "call_1", "content": "(no tool output)"}
        ]


# ---------------------------------------------------------------- build_params


def test_zai_thinking_on_with_tools():
    params = build_params(
        _model(ZAI),
        [WEATHER],
        StreamOptions(api_key="k", thinking="high"),
        ZAI,
    )
    assert params["model"] == "test-model"
    assert params["stream"] is True
    assert params["stream_options"] == {"include_usage": True}
    assert params["thinking"] == {"type": "enabled", "clear_thinking": False}
    assert params["reasoning_effort"] == "high"
    assert params["tool_stream"] is True
    assert params["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get current weather for a city",
                "parameters": WEATHER_SCHEMA,
            },
        }
    ]
    # messages are merged by the caller; tool_choice is not set in v1
    assert "messages" not in params
    assert "tool_choice" not in params


def test_zai_thinking_off_disables_and_omits_reasoning_effort():
    params = build_params(
        _model(ZAI), [], StreamOptions(api_key="k", thinking=None), ZAI
    )
    assert params["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in params
    assert "tools" not in params
    assert "tool_stream" not in params


def test_deepseek_thinking_off_is_disabled():
    params = build_params(_model(DEEPSEEK), [], StreamOptions(api_key="k"), DEEPSEEK)
    assert params["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in params
    assert "tool_stream" not in params


def test_deepseek_thinking_on_is_enabled_without_clear_thinking():
    params = build_params(
        _model(DEEPSEEK), [], StreamOptions(api_key="k", thinking="high"), DEEPSEEK
    )
    assert params["thinking"] == {"type": "enabled"}
    assert "clear_thinking" not in params["thinking"]
    assert "reasoning_effort" not in params  # deepseek does not support it


def test_deepseek_with_tools_has_no_tool_stream():
    params = build_params(
        _model(DEEPSEEK), [WEATHER], StreamOptions(api_key="k"), DEEPSEEK
    )
    assert params["tools"]
    assert "tool_stream" not in params


def test_no_dialect_bare_reasoning_effort():
    compat = CompatFlags(supports_reasoning_effort=True)
    params = build_params(
        _model(compat), [], StreamOptions(api_key="k", thinking="low"), compat
    )
    assert params["reasoning_effort"] == "low"
    assert "thinking" not in params


def test_no_dialect_without_effort_flag_has_no_thinking_keys():
    params = build_params(
        _model(CompatFlags()),
        [],
        StreamOptions(api_key="k", thinking="high"),
        CompatFlags(),
    )
    assert "thinking" not in params
    assert "reasoning_effort" not in params


def test_max_tokens_field_with_options_precedence():
    compat = CompatFlags(max_tokens_field="max_output_tokens")
    model = _model(compat, max_tokens=8192)
    default = build_params(model, [], StreamOptions(api_key="k"), compat)
    assert default["max_output_tokens"] == 8192
    assert "max_tokens" not in default

    overridden = build_params(
        model, [], StreamOptions(api_key="k", max_tokens=512), compat
    )
    assert overridden["max_output_tokens"] == 512


def test_max_tokens_omitted_when_falsy():
    compat = CompatFlags()
    params = build_params(
        _model(compat, max_tokens=0), [], StreamOptions(api_key="k"), compat
    )
    assert "max_tokens" not in params


def test_temperature_included_only_when_set():
    compat = CompatFlags()
    with_temp = build_params(
        _model(compat), [], StreamOptions(api_key="k", temperature=0.2), compat
    )
    assert with_temp["temperature"] == 0.2
    without_temp = build_params(_model(compat), [], StreamOptions(api_key="k"), compat)
    assert "temperature" not in without_temp


def test_stream_options_omitted_when_usage_not_streamed():
    compat = CompatFlags(supports_usage_in_streaming=False)
    params = build_params(_model(compat), [], StreamOptions(api_key="k"), compat)
    assert "stream_options" not in params
    assert params["stream"] is True


# ----------------------------------------------- packaged-catalog dialects


def test_packaged_catalog_models_build_expected_params():
    catalog = load_catalog()

    glm = find_model(catalog, "glm-5.3")
    params = build_params(
        glm, [WEATHER], StreamOptions(api_key="k", thinking="high"), glm.compat
    )
    assert params["thinking"] == {"type": "enabled", "clear_thinking": False}
    assert params["reasoning_effort"] == "high"
    assert params["tool_stream"] is True

    reasoner = find_model(catalog, "deepseek-flash")
    params = build_params(
        reasoner, [], StreamOptions(api_key="k", thinking="high"), reasoner.compat
    )
    assert params["thinking"] == {"type": "enabled"}
    assert "tool_stream" not in params
    # DeepSeek advertises effort levels, so the level goes on the wire now
    assert params["reasoning_effort"] == "high"

    # gateway GLM has no thinking dialect: nothing thinking-related is sent
    # (two providers ship a "glm-5.3"; the gateway one is the plain dialect)
    plain = next(m for m in catalog["opencode-go"].models if m.id == "glm-5.3")
    params = build_params(
        plain, [], StreamOptions(api_key="k", thinking="high"), plain.compat
    )
    assert "thinking" not in params
    assert "reasoning_effort" not in params


# ---------------------------------------------------------------------------
# Task 9A: thinking level normalization guard
# ---------------------------------------------------------------------------


def test_thinking_off_is_identical_to_none():
    """'off' is a user-level setting: the wire body must not differ from None."""
    for compat in (ZAI, DEEPSEEK):
        body_off = build_params(
            _model(compat), [], StreamOptions(api_key="k", thinking="off"), compat
        )
        body_none = build_params(
            _model(compat), [], StreamOptions(api_key="k", thinking=None), compat
        )
        assert body_off == body_none
        assert body_off["thinking"] == {"type": "disabled"}
        assert "reasoning_effort" not in body_off


def test_invalid_thinking_level_raises_value_error():
    """A bogus level must fail fast rather than ship a dirty reasoning_effort."""
    import pytest

    with pytest.raises(ValueError, match="invalid thinking level"):
        build_params(
            _model(ZAI), [], StreamOptions(api_key="k", thinking="bogus"), ZAI
        )
    with pytest.raises(ValueError):
        build_params(
            _model(ZAI), [], StreamOptions(api_key="k", thinking="high "), ZAI
        )
