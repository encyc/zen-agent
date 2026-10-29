"""Message-level retry for transient provider failures.

The transport retries only on HTTP status codes; a provider can also answer 200
and then fail inside the stream. This retries the *whole assistant call* when
the terminal message looks transient, buffering each attempt so a failed try
never leaks duplicate events to the UI.

Nothing has executed yet when a call fails (tool calls run only after a
successful assistant message), so retrying cannot replay side effects.
"""

from __future__ import annotations

import asyncio
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
) -> AsyncIterator[AssistantEvent]:
    """Drive ``safe_stream`` and retry transient terminal failures."""
    attempt = 0
    while True:
        buffered: list[AssistantEvent] = []
        async for event in safe_stream(stream_fn, model, messages, tools, options):
            buffered.append(event)

        terminal = buffered[-1] if buffered else None
        retryable = (
            terminal is not None
            and terminal.type == "error"
            and terminal.reason == "error"
            and is_retryable(terminal.partial)
        )
        abort = options.abort
        aborted = abort is not None and abort.aborted
        if not retryable or aborted or attempt >= retries:
            for event in buffered:
                yield event
            return

        attempt += 1
        await sleep(base_delay * (2 ** (attempt - 1)))
        if abort is not None and abort.aborted:
            # Cancelled while backing off: surface the failure, do not retry.
            for event in buffered:
                yield event
            return
