"""Anthropic Messages adapter: request mapping and stream assembly.

The stream tests replay synthetic SSE fixtures through the pure parser; the
public ``stream()`` is exercised separately through httpx.MockTransport.
"""

import json
from pathlib import Path

import httpx
import pytest

import zenagent.providers.anthropic_messages as am
from zenagent.events import AbortToken
from zenagent.providers.anthropic_messages import (
    ANTHROPIC_VERSION,
    build_request,
    thinking_budget,
    to_anthropic_messages,
)
from zenagent.providers.catalog import Model
from zenagent.providers.compat import CompatFlags
from zenagent.providers.openai_completions import StreamOptions
from zenagent.providers.registry import stream_fn_for
from zenagent.providers.transport import iter_sse_data
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

FIXTURES = Path(__file__).resolve().parent.parent / "zenagent" / "providers" / "fixtures"


def model(max_tokens: int = 8192, reasoning: bool = True) -> Model:
    return Model(
        id="claude-test",
        provider="opencode-go",
        api="anthropic-messages",
        base_url="https://opencode.ai/inference/go",
        context_window=200_000,
        max_tokens=max_tokens,
        reasoning=reasoning,
        cost=Cost(input=1.0, output=5.0),
        compat=CompatFlags(),
    )


WEATHER = ToolDef(
    name="get_weather",
    description="Get the weather",
    parameters={
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
    },
)


# ---------------------------------------------------------------------------
# request side
# ---------------------------------------------------------------------------


def test_system_prompt_is_lifted_out_of_the_turns():
    system, turns = to_anthropic_messages(
        [
            SystemMessage(content="be brief", timestamp=1),
            SystemMessage(content="and honest", timestamp=2),
            UserMessage(content="hi", timestamp=3),
        ]
    )
    assert system == "be brief\n\nand honest"
    assert turns == [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]


def test_consecutive_user_and_tool_result_turns_are_merged():
    assistant = AssistantMessage(
        content=[ToolCall(id="t1", name="get_weather", arguments={"city": "A"})],
        provider="p",
        model="m",
        usage=Usage(),
        stop_reason="tool_use",
        timestamp=1,
    )
    tool_a = ToolResultMessage(
        tool_call_id="t1", tool_name="get_weather",
        content=[TextContent(text="sunny")], is_error=False, timestamp=2,
    )
    tool_b = ToolResultMessage(
        tool_call_id="t2", tool_name="get_weather",
        content=[TextContent(text="wrong tool")], is_error=True, timestamp=3,
    )
    system, turns = to_anthropic_messages(
        [UserMessage(content="weather?", timestamp=0), assistant, tool_a, tool_b]
    )
    assert system == ""
    assert [turn["role"] for turn in turns] == ["user", "assistant", "user"]
    # two tool results collapsed into one user turn
    assert len(turns[2]["content"]) == 2
    assert turns[2]["content"][0]["type"] == "tool_result"
    assert turns[2]["content"][1]["is_error"] is True
    # assistant tool_use round-trips
    assert turns[1]["content"][0] == {
        "type": "tool_use", "id": "t1", "name": "get_weather", "input": {"city": "A"}
    }


def test_thinking_blocks_round_trip_with_signature():
    assistant = AssistantMessage(
        content=[
            ThinkingContent(thinking="because", signature="sig"),
            TextContent(text="answer"),
        ],
        provider="p",
        model="m",
        usage=Usage(),
        stop_reason="stop",
        timestamp=1,
    )
    _, turns = to_anthropic_messages([assistant])
    blocks = turns[0]["content"]
    assert blocks[0] == {"type": "thinking", "thinking": "because", "signature": "sig"}
    assert blocks[1] == {"type": "text", "text": "answer"}


def test_redacted_thinking_round_trips_as_redacted_block():
    assistant = AssistantMessage(
        content=[ThinkingContent(thinking="", signature="opaque", redacted=True)],
        provider="p",
        model="m",
        usage=Usage(),
        stop_reason="stop",
        timestamp=1,
    )
    _, turns = to_anthropic_messages([assistant])
    assert turns[0]["content"][0] == {"type": "redacted_thinking", "data": "opaque"}


def test_image_content_maps_to_base64_source():
    _, turns = to_anthropic_messages(
        [UserMessage(content=[ImageContent(data="AAA", mime_type="image/png")], timestamp=1)]
    )
    source = turns[0]["content"][0]["source"]
    assert source == {"type": "base64", "media_type": "image/png", "data": "AAA"}


def test_request_body_has_required_anthropic_fields():
    url, headers, body = build_request(
        model(),
        [SystemMessage(content="sys", timestamp=1), UserMessage(content="hi", timestamp=2)],
        [WEATHER],
        StreamOptions(api_key="k", max_tokens=512),
    )
    assert url == "https://opencode.ai/inference/go/v1/messages"
    assert headers["x-api-key"] == "k"
    assert headers["anthropic-version"] == ANTHROPIC_VERSION
    assert body["model"] == "claude-test"
    assert body["max_tokens"] == 512
    assert body["stream"] is True
    assert body["system"] == "sys"
    assert body["tools"][0]["input_schema"]["required"] == ["city"]
    assert "thinking" not in body


def test_thinking_budget_is_clamped_below_max_tokens():
    assert thinking_budget("low", 8192) == 4096
    assert thinking_budget("medium", 8192) == 4096  # clamped to max_tokens // 2
    assert thinking_budget("high", 200_000) == 32768
    assert thinking_budget(None, 8192) is None
    with pytest.raises(ValueError):
        thinking_budget("banana", 8192)


def test_thinking_disables_temperature_and_sets_budget():
    _, _, body = build_request(
        model(), [UserMessage(content="hi", timestamp=1)], [],
        StreamOptions(api_key="k", thinking="high", temperature=0.7),
    )
    assert body["thinking"] == {"type": "enabled", "budget_tokens": 4096}
    assert "temperature" not in body  # rejected alongside extended thinking


def test_temperature_survives_without_thinking():
    _, _, body = build_request(
        model(), [UserMessage(content="hi", timestamp=1)], [],
        StreamOptions(api_key="k", thinking="off", temperature=0.7),
    )
    assert body["temperature"] == 0.7
    assert "thinking" not in body


def test_session_id_becomes_the_gateway_header():
    _, headers, _ = build_request(
        model(), [UserMessage(content="hi", timestamp=1)], [],
        StreamOptions(api_key="k", session_id="sess-1"),
    )
    assert headers["x-opencode-session"] == "sess-1"


# ---------------------------------------------------------------------------
# stream side: fixture replay
# ---------------------------------------------------------------------------


async def _fixture_events(name: str, token: AbortToken | None = None, mdl: Model | None = None):
    lines = [
        line + "\n"
        for line in (FIXTURES / name).read_text(encoding="utf-8").splitlines()
    ]

    async def source():
        for line in lines:
            yield line

    async def chunks():
        async for payload in iter_sse_data(source()):
            yield json.loads(payload)

    return [event async for event in am._events_from_chunks(mdl or model(), chunks(), token)]


async def test_text_fixture_assembles_a_clean_turn():
    events = await _fixture_events("anthropic-text.txt")
    types = [event.type for event in events]
    assert types[0] == "start" and types[-1] == "done"
    assert "text_start" in types and "text_delta" in types and "text_end" in types

    final = events[-1].partial
    assert final.stop_reason == "stop"
    assert final.response_id == "msg_text_1"
    assert final.content[0].text == "Hello from Claude"
    assert final.usage.input == 12
    assert final.usage.output == 5
    assert final.usage.cost.total > 0  # catalog pricing applied


async def test_thinking_and_tool_fixture_round_trips_signature_and_arguments():
    events = await _fixture_events("anthropic-thinking-toolcall.txt")
    final = events[-1].partial
    assert events[-1].reason == "tool_use"

    thinking = final.content[0]
    assert isinstance(thinking, ThinkingContent)
    assert thinking.thinking == "I should check the weather."
    assert thinking.signature == "sig-abc123"

    call = final.content[1]
    assert isinstance(call, ToolCall)
    assert call.id == "toolu_1" and call.name == "get_weather"
    assert call.arguments == {"city": "Beijing"}
    assert call.arguments_error is None


async def test_bad_json_arguments_degrade_but_finish():
    events = await _fixture_events("anthropic-badjson.txt")
    final = events[-1].partial
    assert events[-1].type == "done"  # the stream itself completes
    assert events[-1].reason == "tool_use"
    call = final.content[0]
    assert call.arguments == {}
    assert call.arguments_error is not None


async def test_abort_mid_stream_yields_aborted_terminal():
    token = AbortToken()
    token.abort()
    events = await _fixture_events("anthropic-text.txt", token)
    assert events[-1].type == "error"
    assert events[-1].reason == "aborted"


# ---------------------------------------------------------------------------
# wiring / HTTP
# ---------------------------------------------------------------------------


def test_registry_dispatches_anthropic_models():
    assert stream_fn_for(model()) is am.stream


async def test_stream_posts_to_the_messages_endpoint(monkeypatch):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        fixture = (FIXTURES / "anthropic-text.txt").read_text()
        return httpx.Response(200, content=fixture.encode())

    transport = httpx.MockTransport(handler)
    real_client = am.default_client

    def patched():
        return httpx.AsyncClient(transport=transport)

    monkeypatch.setattr(am, "default_client", patched)
    events = [
        event
        async for event in am.stream(
            model(),
            [SystemMessage(content="sys", timestamp=1), UserMessage(content="hi", timestamp=2)],
            [],
            StreamOptions(api_key="key-123", max_tokens=256),
        )
    ]

    assert captured["url"] == "https://opencode.ai/inference/go/v1/messages"
    assert captured["headers"]["x-api-key"] == "key-123"
    assert captured["body"]["max_tokens"] == 256
    assert events[-1].type == "done"
    assert events[-1].partial.content[0].text == "Hello from Claude"
    assert real_client is not am.default_client  # sanity: patch scoped


def test_adaptive_thinking_shape_for_gateway_models():
    adaptive = model()
    adaptive.compat = CompatFlags(anthropic_adaptive_thinking=True)
    _, _, body = build_request(
        adaptive, [UserMessage(content="hi", timestamp=1)], [],
        StreamOptions(api_key="k", thinking="high"),
    )
    assert body["thinking"] == {"type": "adaptive"}
    assert body["output_config"] == {"effort": "high"}
    assert "budget_tokens" not in body["thinking"]


def test_thinking_budget_supports_max():
    assert thinking_budget("max", 200_000) == 65536
    assert thinking_budget("max", 4096) == 2048  # clamped below max_tokens


def test_adaptive_thinking_passes_max_effort_through():
    adaptive = model()
    adaptive.compat = CompatFlags(anthropic_adaptive_thinking=True)
    _, _, body = build_request(
        adaptive, [UserMessage(content="hi", timestamp=1)], [],
        StreamOptions(api_key="k", thinking="max"),
    )
    assert body["output_config"] == {"effort": "max"}


async def test_anthropic_cost_includes_cache_components():
    """Anthropic reports cache tokens outside input_tokens; price them too."""
    priced = model()
    priced.cost = Cost(input=1.0, output=5.0, cache_read=0.1, cache_write=1.25)
    events = await _fixture_events("anthropic-text.txt", mdl=priced)
    usage = events[-1].partial.usage
    # fixture: input 12, output 5, no cache tokens
    assert usage.cost.input == pytest.approx(12 / 1_000_000)
    assert usage.cost.output == pytest.approx(25 / 1_000_000)
    assert usage.cost.total == pytest.approx(37 / 1_000_000)


def test_thinking_budget_is_disabled_when_max_tokens_cannot_fit_it():
    from zenagent.providers.anthropic_messages import thinking_budget

    assert thinking_budget("high", 500) is None
    budget = thinking_budget("high", 2_000)
    assert budget is not None and 1024 <= budget < 2_000
