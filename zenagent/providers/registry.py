
from __future__ import annotations

r"""Provider registry: api → stream-fn dispatch and the never-raise wrapper.

Two small pieces that every model API meets at the boundary:

* ``stream_fn_for``: dispatch a catalog ``Model`` to its adapter's ``stream``
  function by ``model.api``. The mapping lives in the module-level
  ``_REGISTRY`` dict; later adapters (e.g. anthropic-messages) plug in with
  ``register_stream_fn`` instead of editing dispatch logic.
* ``safe_stream``: wrap any stream fn so the consumer's ``async for`` never
  sees an exception. If the fn raises — before the first event or mid-stream,
  e.g. a connection reset after deltas already flowed — exactly one terminal
  event is produced instead, carrying whatever was known: the last live
  ``partial`` snapshot, or a minimal shell message when not even that exists.
  ``Aborted`` (cooperative cancellation) maps to ``reason="aborted"``; any
  other ``Exception`` maps to ``reason="error"``. A fn that raises *after* its terminal event yields no
  second terminal (defensive; a well-formed fn never does this).

``default_client`` is re-exported from ``transport`` so callers need only
import this module.
"""

import time
from collections.abc import AsyncIterator, Callable

from zenagent.events import Aborted, AssistantEvent
from zenagent.providers import (
    anthropic_messages,
    google_generative_ai,
    openai_completions,
    openai_responses,
)
from zenagent.providers.catalog import Model
from zenagent.providers.openai_completions import StreamOptions
from zenagent.providers.transport import default_client
from zenagent.types import AssistantMessage, Message, ToolDef, Usage

__all__ = [
    "StreamFn",
    "default_client",
    "register_stream_fn",
    "safe_stream",
    "stream_fn_for",
]

#: A model API's streaming entry point.
StreamFn = Callable[
    [Model, list[Message], list[ToolDef], StreamOptions],
    AsyncIterator[AssistantEvent],
]

#: api name → stream fn. Mutable on purpose: ``register_stream_fn`` extends it.
_REGISTRY: dict[str, StreamFn] = {
    "openai-completions": openai_completions.stream,
    "anthropic-messages": anthropic_messages.stream,
    "openai-responses": openai_responses.stream,
    "google-generative-ai": google_generative_ai.stream,
}

#: Exception-message cap for error partials.
_MAX_ERROR_MESSAGE = 500


def register_stream_fn(api: str, fn: StreamFn) -> None:
    """Register (or replace) the stream fn for ``api``."""
    _REGISTRY[api] = fn


def stream_fn_for(model: Model) -> StreamFn:
    """Return the registered stream fn for ``model.api``.

    Raises KeyError naming the api when no adapter is registered.
    """
    try:
        return _REGISTRY[model.api]
    except KeyError:
        raise KeyError(f"no stream fn registered for api {model.api!r}") from None


def _error_partial(model: Model, error_message: str) -> AssistantMessage:
    """Minimal shell message for a fn that died before yielding anything."""
    return AssistantMessage(
        content=[],
        provider=model.provider,
        model=model.id,
        usage=Usage(),
        stop_reason="error",
        timestamp=int(time.time()),
        error_message=error_message,
    )


def _terminal_error(
    model: Model,
    last_partial: AssistantMessage | None,
    *,
    reason: str,
    error_message: str,
) -> AssistantEvent:
    """Build the single terminal error event, preserving accumulated content."""
    partial = (
        last_partial
        if last_partial is not None
        else _error_partial(model, error_message)
    )
    partial.stop_reason = "aborted" if reason == "aborted" else "error"
    partial.error_message = error_message
    return AssistantEvent(type="error", partial=partial, reason=partial.stop_reason)


async def safe_stream(
    fn: StreamFn,
    model: Model,
    messages: list[Message],
    tools: list[ToolDef],
    options: StreamOptions,
) -> AsyncIterator[AssistantEvent]:
    """Drive ``fn`` and re-yield its events; never let an exception escape.

    If ``fn`` raises at any point, yield one terminal event
    ``type="error", reason="error"`` whose ``partial`` carries what was known
    — the last event's live snapshot (with ``stop_reason``/``error_message``
    filled in) when events had already flowed, else a minimal shell message
    naming the failure. If the fn raised after already emitting a terminal
    event, stop without a second one. Only ``Exception`` is converted;
    ``BaseException`` (task cancellation, KeyboardInterrupt) deliberately
    propagates so cooperative cancellation keeps working.
    """
    last_partial: AssistantMessage | None = None
    saw_terminal = False
    try:
        async for event in fn(model, messages, tools, options):
            last_partial = event.partial
            if event.type in ("done", "error"):
                saw_terminal = True
            yield event
    except Aborted as exc:
        if saw_terminal:
            return
        yield _terminal_error(
            model, last_partial, reason="aborted", error_message=str(exc) or "aborted"
        )
    except Exception as exc:  # noqa: BLE001 - converting ANY fn exception is the contract
        if saw_terminal:
            return
        error_message = f"{type(exc).__name__}: {exc}"[:_MAX_ERROR_MESSAGE]
        yield _terminal_error(
            model, last_partial, reason="error", error_message=error_message
        )
