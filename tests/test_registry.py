"""safe_stream never-raise wrapper and stream-fn registry tests."""

import httpx
import pytest

from pyagent.events import Aborted, AssistantEvent
from pyagent.providers import openai_completions, registry, transport
from pyagent.providers.catalog import Model
from pyagent.providers.compat import CompatFlags
from pyagent.providers.openai_completions import StreamOptions
from pyagent.providers.registry import (
    default_client,
    register_stream_fn,
    safe_stream,
    stream_fn_for,
)
from pyagent.types import AssistantMessage, Cost, Message, TextContent, ToolDef, Usage

TERMINAL_TYPES = {"done", "error"}


def _model(api: str = "openai-completions") -> Model:
    return Model(
        id="test-model",
        provider="test-provider",
        api=api,
        base_url="http://localhost:1/v1",
        context_window=8192,
        max_tokens=1024,
        reasoning=False,
        cost=Cost(),
        compat=CompatFlags(),
    )


def _partial(model: Model) -> AssistantMessage:
    """A live snapshot-so-far, as an adapter attaches to every event."""
    return AssistantMessage(
        content=[],
        provider=model.provider,
        model=model.id,
        usage=Usage(),
        stop_reason="pending",
        timestamp=1,
    )


async def _collect(fn, model: Model) -> list[AssistantEvent]:
    """Run safe_stream to completion with no try/except: an escaping
    exception fails the test, which is exactly what we want to catch."""
    messages: list[Message] = []
    tools: list[ToolDef] = []
    return [
        event
        async for event in safe_stream(fn, model, messages, tools, StreamOptions())
    ]


# ---------------------------------------------------------------------------
# safe_stream: exceptions become exactly one terminal error event
# ---------------------------------------------------------------------------


async def test_mid_stream_raise_yields_single_error_terminal() -> None:
    """Review Focus #2: connection reset after deltas already flowed."""
    model = _model()

    async def flaky(model, messages, tools, options):
        partial = _partial(model)
        yield AssistantEvent(type="start", partial=partial)
        text = TextContent(text="Hel")
        partial.content.append(text)
        yield AssistantEvent(type="text_start", partial=partial, content_index=0)
        text.text += "lo"
        yield AssistantEvent(
            type="text_delta", partial=partial, delta="lo", content_index=0
        )
        raise httpx.ReadError("connection reset mid-stream")

    events = await _collect(flaky, model)

    assert [e.type for e in events[:3]] == ["start", "text_start", "text_delta"]
    terminals = [e for e in events if e.type in TERMINAL_TYPES]
    assert len(terminals) == 1
    last = events[-1]
    assert last.type == "error"
    assert last.reason == "error"
    # partial carries whatever was known: the accumulated live snapshot.
    assert last.partial.content == [TextContent(text="Hello")]
    assert last.partial.stop_reason == "error"
    assert "ReadError" in last.partial.error_message
    assert "connection reset mid-stream" in last.partial.error_message


async def test_raise_before_first_event_yields_exactly_one_error() -> None:
    model = _model()

    async def dead_on_arrival(model, messages, tools, options):
        raise RuntimeError("boom before anything streamed")
        yield  # pragma: no cover - makes this an async generator

    events = await _collect(dead_on_arrival, model)

    assert len(events) == 1
    only = events[0]
    assert only.type == "error"
    assert only.reason == "error"
    # No partial was ever seen: a minimal shell message naming the failure.
    assert only.partial.content == []
    assert only.partial.provider == model.provider
    assert only.partial.model == model.id
    assert only.partial.stop_reason == "error"
    assert "RuntimeError" in only.partial.error_message
    assert "boom" in only.partial.error_message
    assert only.partial.timestamp > 0


async def test_error_message_truncated_to_500_chars() -> None:
    model = _model()

    async def noisy(model, messages, tools, options):
        raise ValueError("x" * 600)
        yield  # pragma: no cover - makes this an async generator

    events = await _collect(noisy, model)

    assert len(events) == 1
    assert len(events[0].partial.error_message) <= 500
    assert events[0].partial.error_message.startswith("ValueError:")


async def test_no_second_terminal_after_terminal_then_raise() -> None:
    """Defensive: a fn that raises after its terminal never yields two."""
    model = _model()

    async def rogue(model, messages, tools, options):
        partial = _partial(model)
        yield AssistantEvent(type="start", partial=partial)
        partial.stop_reason = "stop"
        yield AssistantEvent(type="done", partial=partial, reason="stop")
        raise ValueError("raised after terminal")

    events = await _collect(rogue, model)

    assert [e.type for e in events] == ["start", "done"]
    assert events[-1].reason == "stop"
    assert len([e for e in events if e.type in TERMINAL_TYPES]) == 1


# ---------------------------------------------------------------------------
# safe_stream: clean fns pass through untouched
# ---------------------------------------------------------------------------


async def test_clean_stream_events_are_reyielded_unchanged() -> None:
    model = _model()
    partial = _partial(model)
    expected = [
        AssistantEvent(type="start", partial=partial),
        AssistantEvent(type="done", partial=partial, reason="stop"),
    ]
    produced = list(expected)

    async def clean(model, messages, tools, options):
        for event in produced:
            yield event

    events = await _collect(clean, model)

    assert events == expected
    # identity, not just equality: the very same event objects flow through
    assert all(got is wanted for got, wanted in zip(events, expected, strict=True))


# ---------------------------------------------------------------------------
# Registry dispatch
# ---------------------------------------------------------------------------


def test_stream_fn_for_openai_completions_is_the_adapter() -> None:
    assert stream_fn_for(_model()) is openai_completions.stream


def test_stream_fn_for_unknown_api_raises_key_error() -> None:
    with pytest.raises(KeyError, match="carrier-pigeon"):
        stream_fn_for(_model(api="carrier-pigeon"))


def test_register_stream_fn_then_dispatch() -> None:
    async def fake_stream(model, messages, tools, options):
        yield AssistantEvent(type="start", partial=_partial(model))

    register_stream_fn("anthropic-messages", fake_stream)
    try:
        assert stream_fn_for(_model(api="anthropic-messages")) is fake_stream
    finally:
        del registry._REGISTRY["anthropic-messages"]


def test_register_stream_fn_overwrites_existing_entry() -> None:
    async def replacement(model, messages, tools, options):
        yield AssistantEvent(type="start", partial=_partial(model))

    original = registry._REGISTRY["openai-completions"]
    register_stream_fn("openai-completions", replacement)
    try:
        assert stream_fn_for(_model()) is replacement
    finally:
        register_stream_fn("openai-completions", original)


# ---------------------------------------------------------------------------
# default_client: re-exported from transport, streaming timeouts
# ---------------------------------------------------------------------------


def test_default_client_is_the_transport_one() -> None:
    assert default_client is transport.default_client


async def test_default_client_timeouts() -> None:
    client = default_client()
    try:
        assert client.timeout.connect == 10.0
        assert client.timeout.read == 300.0
    finally:
        await client.aclose()


# ---------------------------------------------------------------------------
# Task 9A: Aborted maps to reason="aborted", other errors stay "error"
# ---------------------------------------------------------------------------


async def test_safe_stream_maps_aborted_to_aborted_terminal_event():
    model = _model()
    fired: list[AssistantEvent] = []

    async def aborted_stream(*_args, **_kwargs):
        partial = _partial(model)
        fired.append(partial)
        yield AssistantEvent(type="start", partial=partial)
        raise Aborted("abort requested before request attempt")

    events = [event async for event in safe_stream(aborted_stream, model, [], [], StreamOptions())]
    assert [event.type for event in events] == ["start", "error"]
    terminal = events[-1]
    assert terminal.reason == "aborted"
    assert terminal.partial.stop_reason == "aborted"
    assert terminal.partial.error_message == "abort requested before request attempt"
    # Accumulated content is preserved, not replaced by a shell message.
    assert terminal.partial.content is fired[0].content


async def test_safe_stream_keeps_error_reason_for_other_exceptions():
    model = _model()

    async def boom(*_args, **_kwargs):
        raise httpx.ReadError("connection reset")
        yield  # pragma: no cover

    events = [event async for event in safe_stream(boom, model, [], [], StreamOptions())]
    assert events[-1].type == "error"
    assert events[-1].reason == "error"
    assert events[-1].partial.stop_reason == "error"
