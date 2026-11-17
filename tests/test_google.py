"""google-generative-ai adapter: request mapping and stream assembly."""

import json
from pathlib import Path

import zenagent.providers.google_generative_ai as g
from zenagent.providers.catalog import Model
from zenagent.providers.compat import CompatFlags
from zenagent.providers.openai_completions import StreamOptions
from zenagent.providers.transport import iter_sse_data
from zenagent.types import (
    AssistantMessage,
    Cost,
    ImageContent,
    SystemMessage,
    TextContent,
    ToolCall,
    ToolDef,
    ToolResultMessage,
    Usage,
    UserMessage,
)

FIXTURES = Path(__file__).resolve().parent.parent / "zenagent" / "providers" / "fixtures"
WEATHER = ToolDef(
    name="get_weather", description="weather",
    parameters={"type": "object", "properties": {"city": {"type": "string"}}},
)


def model(*, model_id: str = "gemini-test", supports_vision: bool = False) -> Model:
    return Model(
        id=model_id, provider="google", api="google-generative-ai",
        base_url="https://g.test/v1beta", context_window=1000, max_tokens=100,
        reasoning=False, cost=Cost(), compat=CompatFlags(),
        supports_vision=supports_vision,
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


# ---------------------------------------------------------------------------
# Tool-result images: Gemini 3 nests them, older models get a user turn
# ---------------------------------------------------------------------------


def _image_result(
    call_id: str, *, text: str = "viewport", is_error: bool = False
) -> ToolResultMessage:
    content = [TextContent(text=text)] if text else []
    content.append(ImageContent(data="aGVsbG8=", mime_type="image/png"))
    return ToolResultMessage(
        tool_call_id=call_id,
        tool_name="screenshot",
        content=content,
        is_error=is_error,
        timestamp=4,
    )


def _screenshot_turn(*call_ids: str) -> AssistantMessage:
    return AssistantMessage(
        content=[
            ToolCall(id=call_id, name="screenshot", arguments={})
            for call_id in call_ids
        ],
        provider="p",
        model="m",
        usage=Usage(),
        stop_reason="tool_use",
        timestamp=3,
    )


def test_tool_result_image_nests_in_function_response_on_gemini_3():
    messages = [_screenshot_turn("c1"), _image_result("c1")]
    _, _, body = g.build_request(
        model(model_id="gemini-3.1-pro-preview", supports_vision=True),
        messages,
        [],
        StreamOptions(api_key="k"),
    )
    assert body["contents"] == [
        {"role": "model", "parts": [{"functionCall": {"name": "screenshot", "args": {}}}]},
        {
            "role": "user",
            "parts": [
                {
                    "functionResponse": {
                        "name": "screenshot",
                        "response": {"output": "viewport"},
                        "parts": [
                            {"inlineData": {"mimeType": "image/png", "data": "aGVsbG8="}}
                        ],
                    }
                }
            ],
        },
    ]


def test_tool_result_image_gets_its_own_user_turn_on_gemini_2():
    messages = [_screenshot_turn("c1"), _image_result("c1")]
    _, _, body = g.build_request(
        model(model_id="gemini-2.5-flash", supports_vision=True),
        messages,
        [],
        StreamOptions(api_key="k"),
    )
    assert body["contents"] == [
        {"role": "model", "parts": [{"functionCall": {"name": "screenshot", "args": {}}}]},
        {
            "role": "user",
            "parts": [
                {"functionResponse": {"name": "screenshot", "response": {"output": "viewport"}}}
            ],
        },
        {
            "role": "user",
            "parts": [
                {"text": "Image returned by tool screenshot (call c1):"},
                {"inlineData": {"mimeType": "image/png", "data": "aGVsbG8="}},
            ],
        },
    ]


def test_a_function_response_after_the_image_turn_starts_a_new_turn():
    """The image turn must not swallow the next tool result's response."""
    messages = [
        _screenshot_turn("c1", "c2", "c3"),
        ToolResultMessage(
            tool_call_id="c1",
            tool_name="screenshot",
            content=[TextContent(text="alpha")],
            is_error=False,
            timestamp=4,
        ),
        _image_result("c2"),
        ToolResultMessage(
            tool_call_id="c3",
            tool_name="screenshot",
            content=[TextContent(text="beta")],
            is_error=False,
            timestamp=5,
        ),
    ]
    _, _, body = g.build_request(
        model(model_id="gemini-2.5-flash", supports_vision=True),
        messages,
        [],
        StreamOptions(api_key="k"),
    )
    assert [content["role"] for content in body["contents"]] == [
        "model",
        "user",
        "user",
        "user",
    ]
    assert body["contents"][3]["parts"] == [
        {"functionResponse": {"name": "screenshot", "response": {"output": "beta"}}}
    ]


def test_tool_result_image_becomes_a_placeholder_without_vision():
    messages = [_screenshot_turn("c1"), _image_result("c1")]
    _, _, body = g.build_request(
        model(model_id="gemini-3.1-pro-preview"),
        messages,
        [],
        StreamOptions(api_key="k"),
    )
    assert body["contents"][1]["parts"] == [
        {
            "functionResponse": {
                "name": "screenshot",
                "response": {
                    "output": (
                        "viewport\n"
                        "(tool image omitted: model does not support images)"
                    )
                },
            }
        }
    ]


def test_error_tool_result_uses_the_documented_error_key():
    messages = [_screenshot_turn("c1"), _image_result("c1", text="boom", is_error=True)]
    _, _, body = g.build_request(
        model(model_id="gemini-3.1-pro-preview", supports_vision=True),
        messages,
        [],
        StreamOptions(api_key="k"),
    )
    function_response = body["contents"][1]["parts"][0]["functionResponse"]
    assert function_response["response"] == {"error": "boom"}


def test_image_only_tool_result_reports_the_attached_image():
    messages = [
        _screenshot_turn("c1"),
        ToolResultMessage(
            tool_call_id="c1",
            tool_name="screenshot",
            content=[ImageContent(data="aGVsbG8=", mime_type="image/png")],
            is_error=False,
            timestamp=4,
        ),
    ]
    _, _, body = g.build_request(
        model(model_id="gemini-3.1-pro-preview", supports_vision=True),
        messages,
        [],
        StreamOptions(api_key="k"),
    )
    function_response = body["contents"][1]["parts"][0]["functionResponse"]
    assert function_response["response"] == {"output": "(see attached image)"}
    assert function_response["parts"][0]["inlineData"]["data"] == "aGVsbG8="


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


async def test_truncated_toolcall_reports_length_not_tool_use():
    events = await _events("google-truncated-toolcall.txt")
    final = events[-1].partial
    assert events[-1].reason == "length"
    assert final.stop_reason == "length"
    assert isinstance(final.content[0], ToolCall)
