"""Tests for the stream side of the OpenAI-compatible adapter.

SSE fixture files under ``pyagent/providers/fixtures`` (one complete
``data:`` JSON chunk per line, exactly what the wire carries, terminated by
``data: [DONE]``) are replayed through ``iter_sse_data`` into an async
generator of parsed chunk dicts and fed to ``_events_from_chunks`` — the
pure chunk→event assembly core. No HTTP is involved except the final wiring
test, which drives the public ``stream()`` through ``httpx.MockTransport``.
"""

import json
from pathlib import Path

import httpx
import pytest

import pyagent.providers.openai_completions as oc
from pyagent.events import AbortToken
from pyagent.providers.catalog import Model
from pyagent.providers.compat import CompatFlags
from pyagent.providers.openai_completions import (
    StreamOptions,
    _events_from_chunks,
    stream,
)
from pyagent.providers.transport import iter_sse_data
from pyagent.types import (
    Cost,
    SystemMessage,
    TextContent,
    ThinkingContent,
    UserMessage,
)

FIXTURES = Path(__file__).resolve().parent.parent / "pyagent" / "providers" / "fixtures"

# Mirrors the catalog's zai dialect (see pyagent/catalog.yaml: zai-coding-cn).
ZAI = CompatFlags(
    thinking_format="zai",
    zai_tool_stream=True,
    supports_developer_role=False,
    supports_reasoning_effort=True,
)


def _model(base_url: str = "https://api.test/v1") -> Model:
    return Model(
        id="glm-5.3",
        provider="zai-coding-cn",
        api="openai-completions",
        base_url=base_url,
        context_window=1000000,
        max_tokens=131072,
        reasoning=True,
        cost=Cost(input=1.0, output=2.0),
        compat=ZAI,
    )


async def _fixture_chunks(name: str):
    """Replay a fixture file as an async generator of parsed chunk dicts.

    Lines (blank SSE separators included) go through ``iter_sse_data``
    exactly as ``_chunk_stream`` frames a live response, so the replay
    exercises the real dispatch and ``[DONE]`` handling.
    """
    lines = [
        line + "\n"
        for line in (FIXTURES / name).read_text(encoding="utf-8").splitlines()
    ]

    async def source():
        for line in lines:
            yield line

    async for payload in iter_sse_data(source()):
        yield json.loads(payload)


async def _replay(name: str) -> list:
    model = _model()
    return [event async for event in _events_from_chunks(model, _fixture_chunks(name))]


async def _inline(chunks: list[dict]) -> list:
    """Feed hand-written chunk dicts straight into the assembly core."""

    async def source():
        for chunk in chunks:
            yield chunk

    return [event async for event in _events_from_chunks(_model(), source())]


def _of_type(events: list, type_: str) -> list:
    return [event for event in events if event.type == type_]


# ------------------------------------------------- scenario 1: text + thinking


async def test_text_plus_thinking_event_sequence_and_usage():
    events = await _replay("zai-glm-5.3-text+thinking.txt")
    assert [event.type for event in events] == [
        "start",
        "thinking_start",
        "thinking_delta",
        "thinking_delta",
        "thinking_end",
        "text_start",
        "text_delta",
        "text_delta",
        "text_end",
        "done",
    ]
    # One partial instance, mutated in place, carried by every event.
    assert all(event.partial is events[0].partial for event in events)
    partial = events[0].partial
    assert partial.provider == "zai-coding-cn"
    assert partial.model == "glm-5.3"
    assert partial.response_id == "chatcmpl-zai-0001"

    thinking_deltas = _of_type(events, "thinking_delta")
    assert [event.delta for event in thinking_deltas] == [
        "The user asks about the ",
        "weather in Tokyo.",
    ]
    thinking_end = _of_type(events, "thinking_end")[0]
    assert thinking_end.content == "The user asks about the weather in Tokyo."

    thinking = partial.content[0]
    assert isinstance(thinking, ThinkingContent)
    assert thinking.thinking == "The user asks about the weather in Tokyo."
    assert thinking.signature == "reasoning_content"  # field it arrived on

    text_start = _of_type(events, "text_start")[0]
    assert text_start.content_index == 1  # thinking precedes text
    text = partial.content[1]
    assert isinstance(text, TextContent)
    assert text.text == "Tokyo in March is mild, around 8-15C."

    done = events[-1]
    assert done.type == "done" and done.reason == "stop"
    assert partial.stop_reason == "stop"
    assert partial.usage.input == 24
    assert partial.usage.output == 18
    assert partial.usage.reasoning == 6
    assert partial.usage.cost.total == pytest.approx(
        24 * 1.0 / 1_000_000 + 18 * 2.0 / 1_000_000
    )
    assert partial.duration_ms is None  # the loop sets duration, not the adapter


# ------------------------------------------------------ scenario 2: tool call


async def test_toolcall_incremental_assembly():
    events = await _replay("zai-glm-5.3-toolcall.txt")
    assert [event.type for event in events] == [
        "start",
        "toolcall_start",
        "toolcall_delta",
        "toolcall_delta",
        "toolcall_end",
        "done",
    ]
    toolcall_start = _of_type(events, "toolcall_start")[0]
    assert toolcall_start.tool_call.id == "call_zai_001"
    assert toolcall_start.tool_call.name == "get_weather"
    assert toolcall_start.content_index == 0

    deltas = _of_type(events, "toolcall_delta")
    assert [event.delta for event in deltas] == ['{"city": ', '"Tokyo", "unit": "celsius"}']

    toolcall_end = _of_type(events, "toolcall_end")[0]
    assert toolcall_end.tool_call.arguments == {"city": "Tokyo", "unit": "celsius"}
    assert toolcall_end.tool_call.arguments_error is None
    assert toolcall_end.tool_call is events[-1].partial.content[0]

    done = events[-1]
    assert done.type == "done" and done.reason == "tool_use"
    assert done.partial.stop_reason == "tool_use"
    assert done.partial.usage.output == 15


# -------------------------------------------------- scenario 3: bad JSON args


async def test_badjson_arguments_degrade_and_stream_still_done():
    events = await _replay("badjson-toolcall.txt")
    assert events[-1].type == "done"  # degradation does not fail the stream
    assert events[-1].reason == "tool_use"

    call = _of_type(events, "toolcall_end")[0].tool_call
    assert call.arguments == {}
    assert call.arguments_error == 'invalid JSON arguments: {"city": Tokyo}'
    assert events[-1].partial.content[0].arguments == {}


# ------------------------------------------- scenario 4: parallel multi-tool


async def test_multi_tool_interleaved_indexes():
    events = await _replay("multi-tool-parallel.txt")
    assert [event.type for event in events] == [
        "start",
        "toolcall_start",
        "toolcall_delta",
        "toolcall_start",
        "toolcall_delta",
        "toolcall_delta",
        "toolcall_delta",
        "toolcall_end",
        "toolcall_end",
        "done",
    ]
    starts = _of_type(events, "toolcall_start")
    assert [event.tool_call.name for event in starts] == ["read_file", "bash"]

    # Interleaved argument fragments route to the right block.
    deltas = _of_type(events, "toolcall_delta")
    assert [event.tool_call.name for event in deltas] == [
        "read_file",
        "bash",
        "read_file",
        "bash",
    ]
    assert [event.delta for event in deltas] == [
        '{"pa',
        '{"cm',
        'th": "a.txt"}',
        'd": "ls"}',
    ]

    ends = _of_type(events, "toolcall_end")
    assert [event.tool_call.name for event in ends] == ["read_file", "bash"]
    assert ends[0].tool_call.arguments == {"path": "a.txt"}
    assert ends[1].tool_call.arguments == {"cmd": "ls"}

    partial = events[-1].partial
    assert [type(block).__name__ for block in partial.content] == ["ToolCall", "ToolCall"]
    assert [block.name for block in partial.content] == ["read_file", "bash"]
    assert events[-1].reason == "tool_use"


# ------------------------------------------- scenario 5: deepseek `reasoning`


async def test_deepseek_reasoning_field_and_separate_usage_chunk():
    events = await _replay("deepseek-reasoner.txt")
    assert [event.type for event in events] == [
        "start",
        "thinking_start",
        "thinking_delta",
        "thinking_delta",
        "thinking_end",
        "text_start",
        "text_delta",
        "text_end",
        "done",
    ]
    partial = events[-1].partial
    thinking = partial.content[0]
    assert isinstance(thinking, ThinkingContent)
    assert thinking.thinking == "Let me think about the prime factorization of 84."
    assert thinking.signature == "reasoning"  # the field this fixture used
    assert partial.content[1].text == "84 = 2^2 * 3 * 7"

    assert events[-1].reason == "stop"
    # Usage arrived in a final chunk with empty choices (include_usage style).
    assert partial.usage.output == 24
    assert partial.usage.cache_read == 4
    assert partial.usage.reasoning == 14


# ---------------------------------------------------- scenario 6: mid-abort


async def test_abort_mid_stream_emits_error_and_stops():
    token = AbortToken()

    async def chunks():
        pulled = 0
        async for chunk in _fixture_chunks("zai-glm-5.3-text+thinking.txt"):
            yield chunk
            pulled += 1
            if pulled == 3:
                token.abort()  # fires while the parser pulls the next chunk

    events = [event async for event in _events_from_chunks(_model(), chunks(), token)]
    assert [event.type for event in events] == [
        "start",
        "thinking_start",
        "thinking_delta",
        "thinking_delta",
        "error",
    ]
    error = events[-1]
    assert error.reason == "aborted"
    assert error.partial.stop_reason == "aborted"
    assert "done" not in [event.type for event in events]  # nothing after abort


# ------------------------------- extras: reasoning dedupe / finish mapping


async def test_reasoning_fields_dedupe_first_nonempty_wins():
    chunks = [
        {
            "choices": [
                {
                    "index": 0,
                    # A server echoing the same text into both fields at
                    # once must produce exactly one thinking delta.
                    "delta": {"reasoning_content": "abc", "reasoning": "abc"},
                    "finish_reason": None,
                }
            ]
        },
        {"choices": [{"index": 0, "delta": {"reasoning": "def"}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
    ]
    events = await _inline(chunks)
    deltas = _of_type(events, "thinking_delta")
    assert [event.delta for event in deltas] == ["abc", "def"]
    thinking = events[-1].partial.content[0]
    assert thinking.thinking == "abcdef"
    assert thinking.signature == "reasoning"  # last fragment's field name


async def test_unknown_finish_reason_maps_to_error_event():
    chunks = [
        {"choices": [{"index": 0, "delta": {"content": "hi"}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "content_filter"}]},
    ]
    events = await _inline(chunks)
    last = events[-1]
    assert last.type == "error" and last.reason == "error"
    assert "content_filter" in last.partial.error_message
    assert last.partial.stop_reason == "error"
    assert _of_type(events, "text_end")  # blocks still close before terminal


# ---------------------------------------- extras: tool index fallback to id


async def test_tool_index_fallback_to_id():
    chunks = [
        {
            "choices": [
                {
                    "index": 0,
                    # No chunk-level tool index anywhere: routing by id.
                    "delta": {
                        "tool_calls": [
                            {
                                "id": "call_noi",
                                "type": "function",
                                "function": {
                                    "name": "get_weather",
                                    "arguments": '{"ci',
                                },
                            }
                        ]
                    },
                    "finish_reason": None,
                }
            ]
        },
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [
                            {
                                "id": "call_noi",
                                "function": {"arguments": 'ty": "SF"}'},
                            }
                        ]
                    },
                    "finish_reason": None,
                }
            ]
        },
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
    ]
    events = await _inline(chunks)
    assert len(_of_type(events, "toolcall_start")) == 1
    assert len(_of_type(events, "toolcall_delta")) == 2
    call = _of_type(events, "toolcall_end")[0].tool_call
    assert call.id == "call_noi"
    assert call.arguments == {"city": "SF"}
    assert events[-1].reason == "tool_use"


# ------------------------------------- wiring: public stream() end to end


async def test_stream_wiring_via_mock_transport(monkeypatch):
    fixture = (FIXTURES / "zai-glm-5.3-text+thinking.txt").read_text(encoding="utf-8")
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["json"] = json.loads(request.content)
        return httpx.Response(200, content=fixture.encode())

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(oc, "default_client", lambda: client)

    model = _model(base_url="https://api.zai.test/v1/")  # trailing slash
    messages = [
        SystemMessage(content="You are a coding agent.", timestamp=1),
        UserMessage(content="tokyo weather?", timestamp=2),
    ]
    options = StreamOptions(
        api_key="sk-test",
        session_id="sess-42",
        extra_headers={"x-custom": "yes"},
    )
    events = [event async for event in stream(model, messages, [], options)]

    assert captured["url"] == "https://api.zai.test/v1/chat/completions"
    headers = captured["headers"]
    assert headers["authorization"] == "Bearer sk-test"
    assert headers["accept"] == "text/event-stream"
    assert headers["content-type"] == "application/json"
    assert headers["x-opencode-session"] == "sess-42"
    assert headers["x-custom"] == "yes"

    body = captured["json"]
    assert body["model"] == "glm-5.3"
    assert body["stream"] is True
    assert body["stream_options"] == {"include_usage": True}
    assert body["thinking"] == {"type": "disabled"}
    assert body["messages"] == [
        {"role": "system", "content": "You are a coding agent."},
        {"role": "user", "content": "tokyo weather?"},
    ]
    assert "tools" not in body

    assert events[-1].type == "done"
    assert events[-1].partial.usage.output == 18
    assert events[-1].partial.stop_reason == "stop"


# ---------------------------------------------------------------------------
# Live captures (scripts/capture_sse.py): real wire data beats hand-written
# fixtures. Skipped when the capture is absent (offline checkout).
# ---------------------------------------------------------------------------

DEEPSEEK = CompatFlags(
    thinking_format="deepseek",
    requires_reasoning_content_on_assistant=True,
    supports_developer_role=False,
)


def _deepseek_model(model_id: str = "deepseek-reasoner") -> Model:
    return Model(
        id=model_id,
        provider="deepseek",
        api="openai-completions",
        base_url="https://api.deepseek.com",
        context_window=128000,
        max_tokens=8192,
        reasoning=True,
        cost=Cost(input=0.55, output=2.19),
        compat=DEEPSEEK,
    )


async def _replay_live(name: str, model: Model | None = None) -> list:
    chunks = _fixture_chunks(name)
    return [
        event
        async for event in _events_from_chunks(model or _deepseek_model(), chunks)
    ]


async def test_live_deepseek_reasoning_capture_assembles_thinking_then_text():
    name = "deepseek-reasoner-live-thinking.txt"
    if not (FIXTURES / name).is_file():
        pytest.skip(f"live capture missing: {name}")
    events = await _replay_live(name, _deepseek_model())

    types = [event.type for event in events]
    assert types[0] == "start"
    assert types[-1] == "done"
    assert "thinking_start" in types and "thinking_delta" in types and "thinking_end" in types
    assert "text_start" in types and "text_delta" in types
    # thinking must precede text in the assembled content
    thinking_at = types.index("thinking_start")
    text_at = types.index("text_start")
    assert thinking_at < text_at

    final = events[-1].partial
    assert final.stop_reason == "stop"
    assert final.response_id  # captured from the wire
    assert final.usage.input > 0 and final.usage.output > 0
    assert final.usage.reasoning is not None
    thinking = final.content[0]
    assert isinstance(thinking, ThinkingContent) and thinking.thinking
    assert any(isinstance(block, TextContent) and block.text for block in final.content)


async def test_live_deepseek_toolcall_capture_assembles_arguments():
    name = "deepseek-chat-live-toolcall.txt"
    if not (FIXTURES / name).is_file():
        pytest.skip(f"live capture missing: {name}")
    events = await _replay_live(name, _deepseek_model("deepseek-chat"))

    assert events[-1].type == "done"
    assert events[-1].reason == "tool_use"
    types = [event.type for event in events]
    assert "toolcall_start" in types and "toolcall_end" in types

    from pyagent.types import ToolCall

    calls = [block for block in events[-1].partial.content if isinstance(block, ToolCall)]
    assert len(calls) == 1
    assert calls[0].name == "get_weather"
    assert calls[0].arguments.get("city")
    assert calls[0].arguments_error is None


async def test_live_deepseek_text_capture_is_a_clean_text_turn():
    name = "deepseek-chat-live-text.txt"
    if not (FIXTURES / name).is_file():
        pytest.skip(f"live capture missing: {name}")
    events = await _replay_live(name, _deepseek_model("deepseek-chat"))

    assert events[-1].type == "done"
    assert events[-1].reason == "stop"
    final = events[-1].partial
    assert any(isinstance(block, TextContent) and block.text for block in final.content)
    assert not any(isinstance(block, ThinkingContent) for block in final.content)
