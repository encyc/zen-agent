r"""Shared HTTP/SSE transport for provider adapters.

Three concerns live here so no adapter reinvents them:

- ``iter_sse_data``: extract complete SSE ``data:`` payloads from an
  arbitrarily-chunked text stream (SSE spec: multi-line data joined with
  newlines, comment and ``event:/id:/retry:`` lines ignored, ``data: [DONE]``
  terminates, CRLF/CR/LF line endings all honored).
- ``post_stream``: POST returning an unread streaming response, with
  idempotent retry during the *request establishment* phase only (until
  response headers arrive). Once the body stream has begun the call is no
  longer idempotent, and retrying is the caller's problem.
- ``parse_usage``: OpenAI-style usage dicts (``prompt_tokens`` & co.) to the
  unified ``Usage`` model, with optional per-million pricing.
"""

import asyncio
from collections.abc import AsyncIterable, AsyncIterator

import httpx

from pyagent.events import Aborted, AbortToken
from pyagent.types import Cost, Usage

__all__ = [
    "RETRYABLE_STATUS_CODES",
    "default_client",
    "iter_sse_data",
    "parse_usage",
    "post_stream",
    "should_retry",
]

#: Statuses worth retrying: rate limiting plus transient server errors.
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


def _process_line(line: str, data_lines: list[str]) -> str:
    """Fold one complete SSE line into the pending block.

    Returns ``"done"`` (the ``[DONE]`` sentinel was seen), ``"dispatch"``
    (blank line: the pending block is complete) or ``"skip"``.
    """
    if not line:
        return "dispatch"
    if line.startswith(":"):
        return "skip"  # comment line
    name, _, value = line.partition(":")
    value = value.removeprefix(" ")  # spec: strip a single leading space after the colon
    if name == "data":
        if value == "[DONE]":
            return "done"
        data_lines.append(value)
    # event:/id:/retry: and unknown fields are consumed but ignored.
    return "skip"


async def iter_sse_data(line_iter: AsyncIterable[str]) -> AsyncIterator[str]:
    r"""Yield complete SSE data payloads from an async text stream.

    Input items are complete-or-partial lines: one logical line may be split
    across any number of items, so text is accumulated until a terminator
    (``\n``, ``\r`` or ``\r\n``) or stream end completes it. Consecutive
    ``data:`` lines of one block join with ``\n`` per the SSE spec, and a
    block is yielded only when a blank line completes it. The literal payload
    ``[DONE]`` terminates iteration without being yielded. A trailing block
    cut off by stream end (no blank line) is dropped, per spec.
    """
    buffer = ""
    data_lines: list[str] = []
    async for chunk in line_iter:
        buffer += chunk
        while buffer:
            newline = buffer.find("\n")
            carriage = buffer.find("\r")
            if carriage != -1 and (newline == -1 or carriage < newline):
                if carriage == len(buffer) - 1:
                    break  # lone trailing \r: wait — the next byte may complete \r\n
                end = carriage + 2 if buffer[carriage + 1] == "\n" else carriage + 1
                line, buffer = buffer[:carriage], buffer[end:]
            elif newline != -1:
                line, buffer = buffer[:newline], buffer[newline + 1 :]
            else:
                break  # mid-line split: accumulate until a terminator arrives
            action = _process_line(line, data_lines)
            if action == "done":
                return
            if action == "dispatch" and data_lines:
                yield "\n".join(data_lines)
                data_lines.clear()
    if buffer:  # stream ended mid-line: the tail still counts as one line
        tail = buffer.removesuffix("\r")
        if _process_line(tail, data_lines) == "done":
            return
        # No dispatch here: a block without a completing blank line is dropped.


def should_retry(status: int | None, exc: Exception | None) -> bool:
    """Retry policy: rate-limit/5xx statuses and transport-level errors only.

    Client errors (400/401/404 ...), non-transport exceptions, and no-op
    calls (both arguments None) are never retryable.
    """
    if status is not None and status in RETRYABLE_STATUS_CODES:
        return True
    if exc is not None:
        return isinstance(exc, (httpx.TransportError, OSError))
    return False


def default_client() -> httpx.AsyncClient:
    """Client with streaming-appropriate timeouts (connect 10s, read 300s)."""
    return httpx.AsyncClient(
        timeout=httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=10.0),
    )


async def _sleep(delay: float, abort: AbortToken | None = None) -> None:
    """Backoff sleep, interruptible by ``abort``; module-level so tests can patch it.

    Raises :class:`~pyagent.events.Aborted` when the token fires while waiting,
    so a cancelled run does not sit out the full backoff.
    """
    if abort is None:
        await asyncio.sleep(delay)
        return
    if abort.aborted:
        raise Aborted("abort requested during backoff")
    sleep_task = asyncio.ensure_future(asyncio.sleep(delay))
    abort_task = asyncio.ensure_future(abort.wait())
    try:
        await asyncio.wait({sleep_task, abort_task}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in (sleep_task, abort_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(sleep_task, abort_task, return_exceptions=True)
    if abort.aborted:
        raise Aborted("abort requested during backoff")


def _retry_after_seconds(headers: httpx.Headers) -> float | None:
    """Parse the seconds form of ``Retry-After``; None when absent or invalid."""
    raw = headers.get("retry-after")
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None  # HTTP-date form (or garbage): fall back to exponential backoff


def _backoff_delay(
    attempt: int,
    retry_after: float | None,
    base_s: float,
    cap_s: float,
) -> float:
    """Retry-After (capped) when the server asked, else exponential backoff."""
    if retry_after is not None:
        return min(retry_after, cap_s)
    return min(base_s * 2 ** (attempt - 1), cap_s)


async def post_stream(
    client: httpx.AsyncClient,
    url: str,
    headers: dict,
    body: dict,
    abort: AbortToken | None = None,
    max_attempts: int = 3,
    backoff_base_s: float = 1.0,
    backoff_cap_s: float = 30.0,
) -> httpx.Response:
    """POST ``body`` and return the unread streaming response.

    Retries only while the request is idempotent — request establishment
    (connection, send, waiting for headers) — never after the body stream has
    begun. Retryable failures are statuses in :data:`RETRYABLE_STATUS_CODES`
    and transport-level exceptions (see ``should_retry``). ``Retry-After``
    (seconds form) overrides the exponential backoff when present, capped at
    ``backoff_cap_s``. Non-retryable HTTP error statuses raise
    ``httpx.HTTPStatusError`` immediately; exhausted retries raise the last
    error. An aborted ``abort`` token raises :class:`~pyagent.events.Aborted`
    before any attempt, and interrupts a backoff wait.
    """
    for attempt in range(1, max_attempts + 1):
        if abort is not None and abort.aborted:
            raise Aborted("abort requested before request attempt")
        request = client.build_request("POST", url, headers=headers, json=body)
        try:
            response = await client.send(request, stream=True)
        except Exception as exc:
            if attempt >= max_attempts or not should_retry(None, exc):
                raise
            await _sleep(
                _backoff_delay(attempt, None, backoff_base_s, backoff_cap_s), abort
            )
            continue
        if should_retry(response.status_code, None):
            retry_after = _retry_after_seconds(response.headers)
            await response.aclose()
            if attempt >= max_attempts:
                response.raise_for_status()  # exhausted: surface the last status error
            await _sleep(
                _backoff_delay(attempt, retry_after, backoff_base_s, backoff_cap_s),
                abort,
            )
            continue
        if not response.is_success:
            await response.aclose()
            response.raise_for_status()
        return response
    raise AssertionError("unreachable: post_stream must return or raise inside the loop")


def parse_usage(chunk_usage: dict, prices: Cost | None = None) -> Usage:
    """Map an OpenAI-style usage dict onto ``Usage``.

    ``prompt_tokens``/``completion_tokens`` map to input/output;
    ``prompt_tokens_details.cached_tokens`` to cache_read; ``reasoning_tokens``
    (top-level, or inside ``completion_tokens_details``) to reasoning;
    ``total_tokens`` is ignored. Missing keys default to zero/None.
    ``prices`` are USD per one million tokens: when given, cost is
    input/output token counts scaled by the respective rates; without prices
    the cost stays zero-valued.
    """
    prompt_details = chunk_usage.get("prompt_tokens_details") or {}
    completion_details = chunk_usage.get("completion_tokens_details") or {}
    input_tokens = chunk_usage.get("prompt_tokens", 0)
    output_tokens = chunk_usage.get("completion_tokens", 0)
    reasoning = chunk_usage.get("reasoning_tokens")
    if reasoning is None:
        reasoning = completion_details.get("reasoning_tokens")
    cost = Cost()
    if prices is not None:
        cost_input = input_tokens * prices.input / 1_000_000
        cost_output = output_tokens * prices.output / 1_000_000
        cost = Cost(input=cost_input, output=cost_output, total=cost_input + cost_output)
    return Usage(
        input=input_tokens,
        output=output_tokens,
        cache_read=prompt_details.get("cached_tokens", 0),
        cache_write=0,
        reasoning=reasoning,
        cost=cost,
    )
