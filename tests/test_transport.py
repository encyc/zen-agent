"""Transport tests: SSE data extraction, retry policy, POST streaming, usage parsing.

Fully offline: SSE inputs are hand-written async generators; ``post_stream`` is
exercised through ``httpx.MockTransport``.
"""

import asyncio
import json

import httpx
import pytest

from zenagent.events import Aborted, AbortToken
from zenagent.providers import transport
from zenagent.providers.transport import (
    default_client,
    iter_sse_data,
    parse_usage,
    post_stream,
    should_retry,
)
from zenagent.types import Cost, Usage

URL = "https://api.test/v1/chat"


async def _collect(chunks: list[str]) -> list[str]:
    """Feed hand-written chunk yields into iter_sse_data and collect payloads."""

    async def line_iter():
        for chunk in chunks:
            yield chunk

    return [payload async for payload in iter_sse_data(line_iter())]


# --------------------------------------------------------------------------
# iter_sse_data
# --------------------------------------------------------------------------


async def test_sse_json_split_across_chunks_then_done():
    # Brief Step-1 (a): one JSON payload artificially split across two chunks.
    # Terminators are in-band; the "" item contributes nothing (partial-line
    # accumulation means an empty item is not a blank line).
    chunks = ['data: {"a":', "1}\n", "", "\n", "data: [DONE]"]
    assert await _collect(chunks) == ['{"a":1}']


async def test_sse_ignores_comments_fields_and_handles_crlf():
    chunks = [
        ": keep-alive\r\n",
        "event: message\r\n",
        "id: 7\r\n",
        "retry: 250\r\n",
        'data: {"a":',
        "1}\r\n",
        "\r\n",
        "data: [DONE]\r\n",
        "\r\n",
    ]
    assert await _collect(chunks) == ['{"a":1}']


async def test_sse_midline_split_at_arbitrary_points():
    # Review Focus #1: a single line may arrive split across several items —
    # here even the "data" field name itself is cut in half.
    chunks = ["da", "ta: hel", "lo\n", "\n", "data: [DONE]\n", "\n"]
    assert await _collect(chunks) == ["hello"]


async def test_sse_multiline_data_joins_with_newline():
    chunks = ["data: line1\n", "data: line2\n", "\n", "data: [DONE]\n\n"]
    assert await _collect(chunks) == ["line1\nline2"]


async def test_sse_multiple_events_before_done():
    assert await _collect(["data: a\n\n", "data: b\n\n", "data: [DONE]\n\n"]) == ["a", "b"]


async def test_sse_cr_only_line_endings():
    # Lone CR terminates lines per spec; the trailing CR before stream end is
    # still recognized as a terminator (not the first half of a CRLF).
    assert await _collect(["data: y\r\rdata: [DONE]\r"]) == ["y"]


async def test_sse_strips_single_leading_space_after_colon():
    assert await _collect(["data:  pad\n\n", "data: [DONE]\n\n"]) == [" pad"]


async def test_sse_done_stops_iteration_immediately():
    assert await _collect(["data: x\n\n", "data: [DONE]\n\n", "data: y\n\n"]) == ["x"]


async def test_sse_block_without_final_blank_line_is_dropped_at_eof():
    # Spec: an event dispatches only on a blank line. A stream that ends
    # mid-block must not yield its partial payload.
    assert await _collect(["data: tail"]) == []


# --------------------------------------------------------------------------
# should_retry
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "exc", "expected"),
    [
        (429, None, True),
        (500, None, True),
        (502, None, True),
        (503, None, True),
        (504, None, True),
        (200, None, False),
        (302, None, False),
        (400, None, False),
        (401, None, False),
        (404, None, False),
        (None, httpx.ConnectError("refused"), True),
        (None, httpx.ReadTimeout("timed out"), True),
        (None, OSError("broken pipe"), True),
        (None, ValueError("nope"), False),
        (None, None, False),
    ],
)
def test_should_retry_truth_table(status, exc, expected):
    assert should_retry(status, exc) is expected


def test_should_retry_http_status_error_is_not_retryable():
    request = httpx.Request("POST", URL)
    exc = httpx.HTTPStatusError("400", request=request, response=httpx.Response(400))
    assert should_retry(None, exc) is False


# --------------------------------------------------------------------------
# post_stream
# --------------------------------------------------------------------------


def _record_sleep(monkeypatch) -> list[float]:
    """Replace transport backoff sleep with a recorder; returns the delays."""
    delays: list[float] = []

    async def fake_sleep(delay: float, abort=None) -> None:
        delays.append(delay)

    monkeypatch.setattr(transport, "_sleep", fake_sleep)
    return delays


async def test_post_stream_retries_500_then_429_then_succeeds(monkeypatch):
    delays = _record_sleep(monkeypatch)
    calls: list[httpx.Request] = []

    async def streaming_body():  # async-iterable body: unread state is observable
        yield b'{"ok": '
        yield b"true}"

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(500)
        if len(calls) == 2:
            return httpx.Response(429, headers={"Retry-After": "3"})
        return httpx.Response(200, content=streaming_body())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        response = await post_stream(
            client,
            URL,
            headers={"Authorization": "Bearer tok"},
            body={"model": "m", "stream": True},
            max_attempts=3,
            backoff_base_s=0.02,
            backoff_cap_s=5.0,
        )
        assert response.status_code == 200
        assert not response.is_stream_consumed  # body left unread for the caller
        assert await response.aread() == b'{"ok": true}'
        await response.aclose()

    assert len(calls) == 3  # exactly two retries
    assert delays == [0.02, 3.0]  # exponential backoff, then Retry-After honored
    request = calls[0]
    assert request.method == "POST"
    assert request.headers["authorization"] == "Bearer tok"
    assert json.loads(request.content) == {"model": "m", "stream": True}


async def test_post_stream_400_raises_status_error_without_retry():
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(400, json={"error": "bad request"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await post_stream(
                client, URL, headers={}, body={}, max_attempts=3, backoff_base_s=0.01
            )
    assert exc_info.value.response.status_code == 400
    assert len(calls) == 1  # client errors are never retried


async def test_post_stream_exhausted_retries_raises_last_status_error(monkeypatch):
    delays = _record_sleep(monkeypatch)
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(503, headers={"Retry-After": "60"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await post_stream(
                client,
                URL,
                headers={},
                body={},
                max_attempts=3,
                backoff_base_s=1.0,
                backoff_cap_s=0.05,
            )
    assert exc_info.value.response.status_code == 503
    assert len(calls) == 3
    assert delays == [0.05, 0.05]  # Retry-After capped at backoff_cap_s


async def test_post_stream_retries_transport_error_then_succeeds():
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ConnectError("connection refused")
        return httpx.Response(200, content=b"data: hi\n\n")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        response = await post_stream(
            client, URL, headers={}, body={}, max_attempts=3, backoff_base_s=0.001
        )
        assert response.status_code == 200
        await response.aclose()
    assert len(calls) == 2


async def test_post_stream_aborted_before_first_attempt():
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200)

    token = AbortToken()
    token.abort()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(Aborted):
            await post_stream(client, URL, headers={}, body={}, abort=token)
    assert calls == []  # aborted before any attempt: no request was sent


async def test_post_stream_abort_between_attempts_cancels():
    token = AbortToken()
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        token.abort()  # aborted while attempt 1's failure is being processed
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(Aborted):
            await post_stream(
                client, URL, headers={}, body={}, abort=token, max_attempts=3, backoff_base_s=0.0
            )
    assert len(calls) == 1  # attempt 2 never starts


# --------------------------------------------------------------------------
# parse_usage
# --------------------------------------------------------------------------


def test_parse_usage_full_openai_shape():
    usage = parse_usage(
        {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,  # ignored (derived downstream)
            "prompt_tokens_details": {"cached_tokens": 40},
            "completion_tokens_details": {"reasoning_tokens": 12},
        }
    )
    assert usage == Usage(
        input=100, output=50, cache_read=40, cache_write=0, reasoning=12, cost=Cost()
    )


def test_parse_usage_missing_keys_default():
    assert parse_usage({}) == Usage()
    assert parse_usage({"prompt_tokens": 5}) == Usage(input=5)
    # null detail dicts are tolerated
    assert parse_usage({"prompt_tokens_details": None}) == Usage()


def test_parse_usage_reasoning_tokens_top_level_wins():
    usage = parse_usage(
        {
            "prompt_tokens": 10,
            "completion_tokens": 3,
            "reasoning_tokens": 7,
            "completion_tokens_details": {"reasoning_tokens": 99},
        }
    )
    assert usage.reasoning == 7


def test_parse_usage_computes_cost_from_per_million_prices():
    usage = parse_usage(
        {"prompt_tokens": 1_000_000, "completion_tokens": 500_000},
        prices=Cost(input=3.0, output=15.0),
    )
    assert usage.input == 1_000_000
    assert usage.output == 500_000
    assert usage.cost.input == pytest.approx(3.0)
    assert usage.cost.output == pytest.approx(7.5)
    assert usage.cost.total == pytest.approx(10.5)
    # no prices: cost stays zero-valued
    assert parse_usage({"prompt_tokens": 10, "completion_tokens": 2}).cost == Cost()


# --------------------------------------------------------------------------
# default_client
# --------------------------------------------------------------------------


async def test_default_client_streaming_timeouts():
    client = default_client()
    assert client.timeout.connect == 10.0
    assert client.timeout.read == 300.0
    await client.aclose()


# --------------------------------------------------------------------------
# Abort: cooperative cancellation is an ordinary exception (Task 9A)
# --------------------------------------------------------------------------


async def test_post_stream_backoff_abort_does_not_wait_out_the_delay():
    """Abort during a backoff wait must return promptly with Aborted."""
    token = AbortToken()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        async def abort_soon() -> None:
            await asyncio.sleep(0.02)
            token.abort()

        task = asyncio.create_task(abort_soon())
        started = asyncio.get_running_loop().time()
        with pytest.raises(Aborted):
            await post_stream(
                client,
                URL,
                headers={},
                body={},
                abort=token,
                max_attempts=3,
                backoff_base_s=30.0,  # would stall for 30s if abort were not observed
                backoff_cap_s=30.0,
            )
        elapsed = asyncio.get_running_loop().time() - started
        await task
    assert elapsed < 1.0, f"backoff was not interrupted (waited {elapsed:.2f}s)"


# --------------------------------------------------------------------------
# cache-aware pricing
# --------------------------------------------------------------------------


def test_parse_usage_prices_cached_tokens_at_the_cache_rate():
    """OpenAI-style prompt_tokens includes cached tokens; they bill differently."""
    usage = parse_usage(
        {
            "prompt_tokens": 1000,
            "completion_tokens": 500,
            "prompt_tokens_details": {"cached_tokens": 400},
        },
        Cost(input=1.0, output=2.0, cache_read=0.1),
    )
    # 600 uncached * $1/M, 400 cached * $0.1/M, 500 out * $2/M
    assert usage.cache_read == 400
    assert usage.cost.input == pytest.approx(0.0006)
    assert usage.cost.cache_read == pytest.approx(0.00004)
    assert usage.cost.output == pytest.approx(0.001)
    assert usage.cost.total == pytest.approx(0.00164)


def test_parse_usage_never_makes_cached_tokens_free():
    """No cache price configured -> fall back to the input rate, do not undercount."""
    usage = parse_usage(
        {"prompt_tokens": 1000, "completion_tokens": 0,
         "prompt_tokens_details": {"cached_tokens": 400}},
        Cost(input=1.0, output=2.0),
    )
    assert usage.cost.cache_read == pytest.approx(0.0004)
    assert usage.cost.input == pytest.approx(0.0006)
    assert usage.cost.total == pytest.approx(0.001)


def test_parse_usage_without_prices_keeps_cost_zero():
    usage = parse_usage(
        {"prompt_tokens": 10, "completion_tokens": 5,
         "prompt_tokens_details": {"cached_tokens": 4}}
    )
    assert usage.cost == Cost()
