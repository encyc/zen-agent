"""Message-level retry for transient provider failures.

The transport retries only on HTTP status codes; a provider can also answer 200
and then fail inside the stream. This retries the *whole assistant call* when
the terminal message looks transient.

Retrying is only safe while nothing has been shown, so an attempt is retried
only if it failed before its first content event: the pre-content events a
connection failure can produce (``start`` and anything before the first
``text_*``/``thinking_*``/``toolcall_*``) are held back, and once content
arrives the attempt streams straight through. A failed connection therefore
retries invisibly *and* a streamed answer stays streamed, which is the whole
reason these two used to exclude each other. A failure after content has
streamed is not retried — a second attempt would duplicate output the user has
already seen.

Nothing has executed yet when a call fails before content (tool calls run only
after a successful assistant message), so retrying cannot replay side effects.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable

from zenagent.events import AssistantEvent
from zenagent.providers.registry import StreamFn, safe_stream
from zenagent.types import AssistantMessage, Message, ToolDef

__all__ = ["is_retryable", "stream_with_retry"]

#: Substrings that suggest a transient failure (matched case-insensitively).
_RETRYABLE = (
    "timeout",
    "timed out",
    "connection reset",
    "reset by peer",
    "connection error",
    # httpx formats some transport failures as ``ConnectError: ...``; the
    # class name has no space, so "connection error" never matches it.
    "connecterror",
    "connect error",
    "connection aborted",
    "connection closed",
    "econnreset",
    "econnrefused",
    "etimedout",
    "network is unreachable",
    "no route to host",
    "temporary failure in name resolution",
    # The wrapper httpcore raises when a peer closes during the handshake or
    # stream: ConnectError(EndOfStream()).
    "endofstream",
    "end of stream",
    "unexpected eof",
    "ssl_error",
    "handshake failure",
    "temporarily unavailable",
    "overloaded",
    "rate limit",
    "too many requests",
    "try again",
    "bad gateway",
    "service unavailable",
    "gateway timeout",
    " 500",
    " 502",
    " 503",
    " 504",
)

#: Substrings that are deterministic and must not be retried.
_NON_RETRYABLE = (
    "quota",
    "insufficient",
    "unauthorized",
    "invalid api key",
    "forbidden",
    "bad request",
    "not found",
    "context length",
    "context window",
    " 400",
    " 401",
    " 403",
    " 404",
)


#: Assistant events that put something in front of the user. Until the first of
#: these arrives an attempt is still invisible, so it can be retried; after one
#: it must stream through, because a second attempt would repeat it.
_CONTENT_EVENTS = frozenset(
    {
        "text_start",
        "text_delta",
        "text_end",
        "thinking_start",
        "thinking_delta",
        "thinking_end",
        "toolcall_start",
        "toolcall_delta",
        "toolcall_end",
    }
)


def is_retryable(message: AssistantMessage) -> bool:
    """True when a failed assistant message looks transient."""
    if message.stop_reason != "error":
        return False
    text = (message.error_message or "").lower()
    if not text:
        return False
    if any(marker in text for marker in _NON_RETRYABLE):
        return False
    return any(marker in text for marker in _RETRYABLE)


async def stream_with_retry(
    stream_fn: StreamFn,
    model,
    messages: list[Message],
    tools: list[ToolDef],
    options,
    *,
    retries: int = 0,
    base_delay: float = 0.5,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    on_retry: Callable[[AssistantMessage, int, int, float], None] | None = None,
) -> AsyncIterator[AssistantEvent]:
    """Drive ``safe_stream`` and retry transient terminal failures.

    With ``retries <= 0`` events pass straight through as they arrive, so a
    streamed answer stays streamed.

    With retries enabled the attempt is still streamed, not buffered: only the
    events preceding the first content event are held back, so a failure that
    happens while connecting retries without the consumer ever seeing the dead
    attempt. Once content has been yielded the attempt streams through and a
    later failure is not retried — see the module docstring.

    ``on_retry(message, attempt, max_retries, delay_s)`` fires just before each
    backoff sleep — the failed attempt's terminal message, the upcoming attempt
    number, the configured cap and the computed delay. A swallowed attempt is
    otherwise invisible to every observer (the session side-channel recorder
    uses this to persist dsh-style retry records).
    """
    if retries <= 0:
        async for event in safe_stream(stream_fn, model, messages, tools, options):
            yield event
        return

    attempt = 0
    while True:
        held: list[AssistantEvent] = []  # pre-content events, not yet shown
        streaming = False
        terminal: AssistantEvent | None = None

        async for event in safe_stream(stream_fn, model, messages, tools, options):
            if event.type in ("done", "error"):
                terminal = event
                continue
            if streaming:
                yield event
            elif event.type in _CONTENT_EVENTS:
                for pending in held:
                    yield pending
                held.clear()
                streaming = True
                yield event
            else:
                held.append(event)

        retryable = (
            terminal is not None
            and terminal.type == "error"
            and terminal.reason == "error"
            and is_retryable(terminal.partial)
        )
        abort = options.abort
        aborted = abort is not None and abort.aborted
        # A held attempt was never shown, so retrying is invisible; once content
        # streamed a second attempt would duplicate it, so retrying stops there.
        if not retryable or streaming or aborted or attempt >= retries:
            for pending in held:
                yield pending
            if terminal is not None:
                yield terminal
            return

        attempt += 1
        delay = base_delay * (2 ** (attempt - 1))
        if on_retry is not None:
            # An observer must not kill the retry (BLE001/S110 by design).
            with contextlib.suppress(Exception):
                on_retry(terminal.partial, attempt, retries, delay)
        await sleep(delay)
        if abort is not None and abort.aborted:
            # Cancelled while backing off: surface the failure, do not retry.
            for pending in held:
                yield pending
            yield terminal
            return
