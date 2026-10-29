"""openai-responses adapter: request mapping and stream assembly."""

import json
from pathlib import Path

import pyagent.providers.openai_responses as resp
from pyagent.providers.catalog import Model
from pyagent.providers.compat import CompatFlags
from pyagent.providers.openai_completions import StreamOptions
from pyagent.providers.transport import iter_sse_data
from pyagent.types import (
    AssistantMessage,
    Cost,
    SystemMessage,
    TextContent,
    ToolCall,
    ToolDef,
    ToolResultMessage,
    Usage,
    UserMessage,
)

FIXTURES = Path(__file__).resolve().parent.parent / "pyagent" / "providers" / "fixtures"

WEATHER = ToolDef(
    name="get_weather",
    description="weather",
    parameters={"type": "object", "properties": {"city": {"type": "string"}}},
)


def model() -> Model:
    return Model(
        id="gpt-test", provider="openai", api="openai-responses",
        base_url="https://api.test/v1", context_window=1000, max_tokens=100,
        reasoning=True, cost=Cost(), compat=CompatFlags(),
    )


def test_request_mapping_uses_instructions_and_input_items():
    messages = [
        SystemMessage(content="be brief", timestamp=1),
        UserMessage(content="hi", timestamp=2),
        AssistantMessage(
            content=[TextContent(text="calling"), ToolCall(id="c1", name="get_weather", arguments={"city": "A"})],
            provider="p", model="m", usage=Usage(), stop_reason="tool_use", timestamp=3,
        ),
        ToolResultMessage(tool_call_id="c1", tool_name="get_weather",
                          content=[TextContent(text="sunny")], is_error=False, timestamp=4),
    ]
    url, headers, body = resp.build_params(model(), messages, [WEATHER], StreamOptions(api_key="k", thinking="high"))
    assert url == "https://api.test/v1/responses"
    assert headers["Authorization"] == "Bearer k"
    assert body["instructions"] == "be brief"
    assert body["input"][0] == {"role": "user", "content": [{"type": "input_text", "text": "hi"}]}
    assert body["input"][1]["role"] == "assistant"  # assistant text turn
    assert body["input"][2]["type"] == "function_call" and body["input"][2]["call_id"] == "c1"
    assert body["input"][3] == {"type": "function_call_output", "call_id": "c1", "output": "sunny"}
    assert body["tools"][0]["type"] == "function"
    assert body["reasoning"] == {"effort": "high"}


async def _events(name: str):
    lines = [line + "\n" for line in (FIXTURES / name).read_text().splitlines()]

    async def source():
        for line in lines:
            yield line

    async def chunks():
        async for payload in iter_sse_data(source()):
            yield json.loads(payload)

    return [event async for event in resp._events_from_chunks(model(), chunks())]


async def test_text_fixture_assembles_deltas_and_usage():
    events = await _events("openai-responses-text.txt")
    types = [event.type for event in events]
    assert types[0] == "start" and types[-1] == "done"
    final = events[-1].partial
    assert final.stop_reason == "stop"
    assert final.response_id == "resp_text_1"
    assert final.content[0].text == "Hello from Responses"
    assert final.usage.input == 20 and final.usage.output == 5
    assert final.usage.cache_read == 8


async def test_toolcall_fixture_assembles_arguments():
    events = await _events("openai-responses-toolcall.txt")
    final = events[-1].partial
    assert events[-1].reason == "tool_use"
    call = final.content[0]
    assert isinstance(call, ToolCall)
    assert call.name == "get_weather" and call.arguments == {"city": "Beijing"}
    assert call.arguments_error is None
