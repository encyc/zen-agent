"""Message-level retry: classification, buffering, limits, and abort."""


from pyagent.events import AbortToken, AssistantEvent
from pyagent.providers.catalog import Model
from pyagent.providers.compat import CompatFlags
from pyagent.providers.openai_completions import StreamOptions
from pyagent.retry import is_retryable, stream_with_retry
from pyagent.types import AssistantMessage, Cost, Usage


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


async def collect(stream_fn, options, retries, sleep=None):
    kwargs = {"retries": retries}
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
    from pyagent.agent.loop import AgentConfig, AgentState, agent_loop

    stream = Scripted(["connection reset", "done"])
    config = AgentConfig(model=model(), system_prompt="s", api_key="k", stream_fn=stream, retries=1)
    state = AgentState()
    events = [event async for event in agent_loop("hi", state, config)]
    assert stream.calls == 2
    assert state.messages[-1].stop_reason == "stop"
    assert events[-1].type == "agent_end"
