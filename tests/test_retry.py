"""Message-level retry: classification, buffering, limits, and abort."""


import asyncio

from zenagent.events import AbortToken, AssistantEvent
from zenagent.providers.catalog import Model
from zenagent.providers.compat import CompatFlags
from zenagent.providers.openai_completions import StreamOptions
from zenagent.retry import is_retryable, stream_with_retry
from zenagent.types import AssistantMessage, Cost, Usage


def model() -> Model:
    return Model(
        id="m", provider="p", api="openai-completions", base_url="https://x/v1",
        context_window=1000, max_tokens=100, reasoning=False, cost=Cost(),
        compat=CompatFlags(),
    )


def assistant(*, stop="error", error: str | None = None) -> AssistantMessage:
    return AssistantMessage(
        content=[], provider="p", model="m", usage=Usage(),
        stop_reason=stop, timestamp=0, error_message=error,
    )


def test_retryable_classification():
    assert is_retryable(assistant(error="request timed out"))
    assert is_retryable(assistant(error="connection reset by peer"))
    assert is_retryable(assistant(error="HTTP 503 service unavailable"))
    assert is_retryable(assistant(error="rate limit exceeded, try again"))
    # httpx transport failures: class name has no space, and the old empty
    # detail (pre-cause-preservation) must still classify as transient.
    assert is_retryable(assistant(error="ConnectError: "))
    assert is_retryable(
        assistant(error="ConnectError: ConnectError(EndOfStream())")
    )
    assert is_retryable(assistant(error="ssl_error_syscall during handshake"))
    assert not is_retryable(assistant(error="insufficient quota"))
    assert not is_retryable(assistant(error="unauthorized: invalid api key"))
    assert not is_retryable(assistant(error="bad request 400"))
    assert not is_retryable(assistant(error="context length exceeded"))
    assert not is_retryable(assistant(stop="stop", error=None))
    assert not is_retryable(assistant(error=None))


class Scripted:
    def __init__(self, outcomes: list[str]) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    async def __call__(self, model_, messages, tools, options):
        self.calls += 1
        outcome = self.outcomes.pop(0) if self.outcomes else "done"
        partial = assistant(stop="pending")
        yield AssistantEvent(type="start", partial=partial)
        if outcome == "done":
            done = assistant(stop="stop")
            yield AssistantEvent(type="done", partial=done, reason="stop")
        else:
            yield AssistantEvent(type="error", partial=assistant(error=outcome), reason="error")


async def collect(stream_fn, options, retries, sleep=None, **extra):
    kwargs = {"retries": retries, **extra}
    if sleep is not None:
        kwargs["sleep"] = sleep
    return [
        event
        async for event in stream_with_retry(
            stream_fn, model(), [], [], options, **kwargs
        )
    ]


async def test_retries_a_transient_failure_and_only_emits_the_final_attempt():
    stream = Scripted(["request timed out", "done"])
    events = await collect(stream, StreamOptions(), retries=1)

    assert stream.calls == 2
    assert [event.type for event in events] == ["start", "done"]
    assert events[-1].partial.stop_reason == "stop"
    # the failed attempt's events were buffered and discarded
    assert not any(event.type == "error" for event in events)


async def test_does_not_retry_a_deterministic_failure():
    stream = Scripted(["insufficient quota"])
    events = await collect(stream, StreamOptions(), retries=3)
    assert stream.calls == 1
    assert events[-1].type == "error"


async def test_exhausted_retries_return_the_last_error():
    stream = Scripted(["timed out", "timed out", "timed out"])
    events = await collect(stream, StreamOptions(), retries=2)
    assert stream.calls == 3
    assert events[-1].type == "error"
    assert "timed out" in (events[-1].partial.error_message or "")


async def test_zero_retries_keeps_the_old_behaviour():
    stream = Scripted(["timed out", "done"])
    events = await collect(stream, StreamOptions(), retries=0)
    assert stream.calls == 1
    assert events[-1].type == "error"


async def test_abort_during_backoff_stops_retrying():
    token = AbortToken()
    stream = Scripted(["timed out", "done"])

    async def sleep(_delay: float) -> None:
        token.abort()

    events = await collect(stream, StreamOptions(abort=token), retries=3, sleep=sleep)
    assert stream.calls == 1
    assert events[-1].type == "error"


async def test_backoff_grows_between_attempts():
    delays: list[float] = []

    async def sleep(delay: float) -> None:
        delays.append(delay)

    stream = Scripted(["timed out", "timed out", "done"])
    await collect(stream, StreamOptions(), retries=2, sleep=sleep)
    assert delays == [0.5, 1.0]


async def test_loop_uses_retries_from_config():
    from zenagent.agent.loop import AgentConfig, AgentState, agent_loop

    stream = Scripted(["connection reset", "done"])
    config = AgentConfig(model=model(), system_prompt="s", api_key="k", stream_fn=stream, retries=1)
    state = AgentState()
    events = [event async for event in agent_loop("hi", state, config)]
    assert stream.calls == 2
    assert state.messages[-1].stop_reason == "stop"
    assert events[-1].type == "agent_end"


async def test_zero_retries_passes_events_through_while_streaming():
    """retries=0 must not buffer: a delta has to arrive before the stream ends."""
    released = asyncio.Event()

    async def stream(model_, messages, tools, options):
        partial = assistant(stop="pending")
        yield AssistantEvent(type="start", partial=partial)
        await released.wait()
        yield AssistantEvent(type="done", partial=assistant(stop="stop"), reason="stop")

    events = stream_with_retry(stream, model(), [], [], StreamOptions(), retries=0)
    first = await asyncio.wait_for(anext(events), timeout=1.0)
    assert first.type == "start"  # arrived while the provider was still streaming
    released.set()
    rest = [event async for event in events]
    assert [event.type for event in rest] == ["done"]


async def test_enabled_retries_still_buffer_an_attempt():
    """With retries>0 an attempt is buffered so a retry cannot leak events."""
    released = asyncio.Event()

    async def stream(model_, messages, tools, options):
        partial = assistant(stop="pending")
        yield AssistantEvent(type="start", partial=partial)
        await released.wait()
        yield AssistantEvent(type="done", partial=assistant(stop="stop"), reason="stop")

    events = stream_with_retry(stream, model(), [], [], StreamOptions(), retries=1)
    pending = asyncio.create_task(anext(events))
    await asyncio.sleep(0.1)
    assert not pending.done(), "an attempt with retry budget must be buffered"
    released.set()
    assert (await pending).type == "start"
    rest = [event async for event in events]
    assert [event.type for event in rest] == ["done"]


async def _null_sleep(_delay: float) -> None:
    """No real backoff in tests."""
    return


async def test_on_retry_reports_each_attempt_before_backoff():
    retries_seen = []

    def on_retry(message, attempt, max_retries, delay):
        retries_seen.append((message.error_message, attempt, max_retries, round(delay, 3)))

    collected = await collect(
        Scripted(["timeout", "connection reset", "done"]),
        StreamOptions(),
        retries=3,
        sleep=_null_sleep,
        on_retry=on_retry,
    )
    assert collected[-1].reason == "stop"
    assert retries_seen == [
        ("timeout", 1, 3, 0.5),
        ("connection reset", 2, 3, 1.0),
    ]


async def test_on_retry_not_called_without_a_retry():
    retries_seen = []
    collected = await collect(
        Scripted(["done"]), StreamOptions(), retries=2, sleep=_null_sleep,
        on_retry=lambda *args: retries_seen.append(args),
    )
    assert collected[-1].reason == "stop"
    assert retries_seen == []
