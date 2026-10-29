"""google-generative-ai adapter: request mapping and stream assembly."""

import json
from pathlib import Path

import pyagent.providers.google_generative_ai as g
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
    name="get_weather", description="weather",
    parameters={"type": "object", "properties": {"city": {"type": "string"}}},
)


def model() -> Model:
    return Model(
        id="gemini-test", provider="google", api="google-generative-ai",
        base_url="https://g.test/v1beta", context_window=1000, max_tokens=100,
        reasoning=False, cost=Cost(), compat=CompatFlags(),
    )


def test_request_mapping_roles_system_and_tools():
    messages = [
        SystemMessage(content="be brief", timestamp=1),
        UserMessage(content="hi", timestamp=2),
        AssistantMessage(
            content=[ToolCall(id="c1", name="get_weather", arguments={"city": "A"})],
            provider="p", model="m", usage=Usage(), stop_reason="tool_use", timestamp=3,
        ),
        ToolResultMessage(tool_call_id="c1", tool_name="get_weather",
                          content=[TextContent(text="sunny")], is_error=False, timestamp=4),
    ]
    url, headers, body = g.build_request(model(), messages, [WEATHER], StreamOptions(api_key="k"))
    assert url.startswith("https://g.test/v1beta/models/gemini-test:streamGenerateContent")
    assert headers["x-goog-api-key"] == "k"
    assert body["systemInstruction"] == {"parts": [{"text": "be brief"}]}
    assert body["contents"][0] == {"role": "user", "parts": [{"text": "hi"}]}
    assert body["contents"][1]["role"] == "model"
    assert body["contents"][1]["parts"][0]["functionCall"]["name"] == "get_weather"
    assert body["contents"][2]["parts"][0]["functionResponse"]["name"] == "get_weather"
    assert body["tools"][0]["functionDeclarations"][0]["name"] == "get_weather"
    assert body["generationConfig"]["maxOutputTokens"] == 100


async def _events(name: str):
    lines = [line + "\n" for line in (FIXTURES / name).read_text().splitlines()]

    async def source():
        for line in lines:
            yield line

    async def chunks():
        async for payload in iter_sse_data(source()):
            yield json.loads(payload)

    return [event async for event in g._events_from_chunks(model(), chunks())]


async def test_text_fixture_assembles_text_and_usage():
    events = await _events("google-text.txt")
    final = events[-1].partial
    assert events[-1].type == "done" and events[-1].reason == "stop"
    assert final.content[0].text == "Hello from Gemini"
    assert final.usage.input == 12 and final.usage.output == 7


async def test_toolcall_fixture_emits_a_complete_tool_call():
    events = await _events("google-toolcall.txt")
    types = [event.type for event in events]
    assert "toolcall_start" in types and "toolcall_end" in types
    final = events[-1].partial
    assert events[-1].reason == "tool_use"
    call = final.content[0]
    assert isinstance(call, ToolCall)
    assert call.name == "get_weather" and call.arguments == {"city": "Beijing"}
