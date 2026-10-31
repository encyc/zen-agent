"""Request-level output-token fitting: prompt + cap must not exceed the window."""

from zenagent.providers.anthropic_messages import build_request as anthropic_request
from zenagent.providers.catalog import Model
from zenagent.providers.compat import CompatFlags
from zenagent.providers.google_generative_ai import build_request as google_request
from zenagent.providers.limits import (
    estimate_prompt_tokens,
    fit_max_tokens,
    fit_stream_options,
)
from zenagent.providers.openai_completions import StreamOptions
from zenagent.types import (
    Cost,
    ImageContent,
    TextContent,
    ToolDef,
    UserMessage,
)


def _model(context_window: int, max_tokens: int) -> Model:
    return Model(
        id="test-model",
        provider="test",
        api="openai-completions",
        base_url="https://api.test/v1",
        context_window=context_window,
        max_tokens=max_tokens,
        reasoning=False,
        cost=Cost(input=0.0, output=0.0),
        compat=CompatFlags(),
    )


def _long_prompt(chars: int = 2000) -> list[UserMessage]:
    return [UserMessage(content="x" * chars, timestamp=0)]


def test_estimate_prompt_tokens_grows_with_text():
    small = estimate_prompt_tokens([UserMessage(content="hi", timestamp=0)])
    large = estimate_prompt_tokens(_long_prompt())
    assert 0 < small < large


def test_fit_max_tokens_is_unchanged_when_everything_fits():
    model = _model(context_window=100_000, max_tokens=8_000)
    assert fit_max_tokens(model, _long_prompt(), 8_000) == 8_000


def test_fit_max_tokens_leaves_room_for_the_prompt():
    model = _model(context_window=2_000, max_tokens=1_600)
    fitted = fit_max_tokens(model, _long_prompt(), 1_600)
    assert fitted is not None
    assert fitted < 1_600
    assert fitted >= 1_024


def test_fit_stream_options_sets_an_explicit_cap_only_when_needed():
    model = _model(context_window=100_000, max_tokens=8_000)
    default = StreamOptions(api_key="k")
    assert fit_stream_options(model, _long_prompt(), default) is default

    tight = _model(context_window=2_000, max_tokens=1_600)
    fitted = fit_stream_options(tight, _long_prompt(), default)
    assert fitted.max_tokens is not None
    assert fitted.max_tokens < 1_600


def test_anthropic_request_fits_max_tokens_to_the_context_window():
    model = _model(context_window=2_000, max_tokens=1_600)
    _, _, body = anthropic_request(
        model, _long_prompt(), [], StreamOptions(api_key="k")
    )
    assert body["max_tokens"] < 1_600
    assert body["max_tokens"] >= 1_024


def test_google_request_fits_max_output_tokens_to_the_context_window():
    model = _model(context_window=2_000, max_tokens=1_600)
    _, _, body = google_request(
        model, _long_prompt(), [], StreamOptions(api_key="k")
    )
    assert body["generationConfig"]["maxOutputTokens"] < 1_600


def test_estimate_counts_wide_characters_conservatively():
    ascii_text = "x" * 400
    wide_text = "汉" * 400
    ascii_estimate = estimate_prompt_tokens(
        [UserMessage(content=ascii_text, timestamp=0)]
    )
    wide_estimate = estimate_prompt_tokens(
        [UserMessage(content=wide_text, timestamp=0)]
    )
    assert wide_estimate > ascii_estimate


def _schema_heavy_tools(count: int = 6) -> list[ToolDef]:
    return [
        ToolDef(
            name=f"tool_{index}",
            description="d" * 800,
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string", "description": "p" * 400}},
            },
        )
        for index in range(count)
    ]


def test_tool_schemas_count_towards_the_prompt_estimate():
    model = _model(context_window=4_000, max_tokens=3_000)
    prompt = _long_prompt(2_500)
    without = fit_max_tokens(model, prompt, 3_000)
    with_tools = fit_max_tokens(model, prompt, 3_000, tools=_schema_heavy_tools())
    assert with_tools < without


def test_image_blocks_are_not_counted_as_base64_text():
    big = UserMessage(
        content=[
            TextContent(text="what is this?"),
            ImageContent(data="A" * 400_000, mime_type="image/png"),
        ],
        timestamp=0,
    )
    estimate = estimate_prompt_tokens([big])
    assert estimate < 5_000, "base64 image data must not be counted char-by-char"


def test_anthropic_request_accounts_for_tools_when_fitting():
    model = _model(context_window=6_000, max_tokens=5_000)
    prompt = _long_prompt(4_000)
    _, _, without = anthropic_request(model, prompt, [], StreamOptions(api_key="k"))
    _, _, with_tools = anthropic_request(
        model, prompt, _schema_heavy_tools(), StreamOptions(api_key="k")
    )
    assert with_tools["max_tokens"] < without["max_tokens"]
