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
from zenagent.types import Cost, UserMessage


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
