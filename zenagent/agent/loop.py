"""The agent loop: turns, tool rounds, hooks, steering, and abort.

``agent_loop`` is the heart of zenagent. Each iteration builds the request
context (system prompt + history, optionally rewritten by ``transform_context``),
streams one assistant turn through the never-raise ``safe_stream`` wrapper,
appends the final assistant message to ``AgentState``, executes any requested
tools (parallel batch, with ``execution_mode="sequential"`` tools serialized in
call order), appends tool results in assistant source order, then consults
``get_steering_messages`` before looping.

Runtime contract highlights:

* The system message is prepended to a *copy* sent to the provider — it never
  lands in ``state.messages``.
* ``agent_end`` is always emitted (try/finally), carrying ``new_messages``,
  every message this run appended, prompt user message included.
* Degraded tool calls (unknown tool, broken arguments, schema-invalid
  arguments, tool exceptions, hooks blocking) become ``is_error`` tool results;
  the loop keeps running. Only an assistant ``error``/``aborted`` stop reason, a
  non-``tool_use`` stop, or every result asking to ``terminate`` ends the run.
* A ``length`` stop with tool calls never executes anything: truncated
  arguments are unsafe, and leaving the assistant turn's ``tool_calls``
  unresolved would poison the *next* request, so every call fails as is_error
  and the model gets one chance to re-issue.
* ``on_update`` progress from a running tool is forwarded as
  ``tool_execution_update`` events; updates arriving after the tool settled are
  dropped.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["LOOP_GUARD_PREFIX", "is_loop_guard_message"]

from zenagent.agent.guard import (
    LOOP_GUARD_PREFIX,
    LOOP_GUARD_TEXT,
    LOOP_GUARD_THRESHOLD,
    calls_signature,
    is_loop_guard_message,
    is_poll_call,
)
from zenagent.agent.protocol import AgentTool, ToolContext, ToolResultAgent
from zenagent.agent.result_budget import MAX_RESULT_BYTES
from zenagent.agent.tool_exec import run_tool_batch, truncated_call_outcome
from zenagent.events import AbortToken, AgentEvent, RetryAttempt
from zenagent.providers.catalog import Model
from zenagent.providers.registry import StreamFn, stream_fn_for
from zenagent.providers.stream_options import StreamOptions
from zenagent.retry import stream_with_retry
from zenagent.types import (
    AssistantMessage,
    ImageContent,
    Message,
    SystemMessage,
    TextContent,
    ToolCall,
    ToolDef,
    ToolResultMessage,
    UserMessage,
)

__all__ = [
    "LOOP_GUARD_PREFIX",
    "AgentConfig",
    "AgentState",
    "AgentTool",
    "ToolContext",
    "ToolResultAgent",
    "agent_loop",
    "is_loop_guard_message",
]


@dataclass
class AgentConfig:
    """Everything one agent run needs: model, prompt framing, tools, hooks.

    Hooks (all optional, all may be sync or async unless typed otherwise):

    * ``before_tool_call(tool_call, args, tool)`` — return ``{"block": True,
      "reason": str}`` to veto the call; the tool result becomes an is_error
      carrying the reason and ``execute`` is never invoked. ``tool`` is the
      resolved :class:`AgentTool`, so policy can read its declarations. A
      2-argument hook is still accepted (the v0.3 signature).
    * ``after_tool_call(tool_call, result)`` — return a ``ToolResultAgent`` to
      replace the result, or a ``{"content"/"is_error"/"terminate"}`` dict to
      patch individual fields.
    * ``get_steering_messages()`` — async; consulted after each tool round.
      Non-empty results are appended to the state as new context.
    * ``transform_context(request_messages)`` — async; rewrites the full
      request list (system message first) for the LLM call only; the state is
      never touched.
    """

    model: Model
    system_prompt: str
    tools: list[AgentTool] = field(default_factory=list)
    thinking: str | None = None
    api_key: str = ""
    session_id: str | None = None
    before_tool_call: Callable | None = None
    after_tool_call: Callable | None = None
    get_steering_messages: Callable[[], Awaitable[list[Message]]] | None = None
    transform_context: Callable[[list[Message]], Awaitable[list[Message]]] | None = None
    stream_fn: StreamFn | None = None  # None -> stream_fn_for(model)
    #: Hard cap on provider requests in one run (None -> module default).
    max_turns: int | None = None
    #: Identical tool-call streak before the loop guard intervenes
    #: (``[limits.agent].loop_guard_threshold``).
    loop_guard_threshold: int = LOOP_GUARD_THRESHOLD
    #: Per-result byte budget before head+tail truncation
    #: (``[limits.agent].max_result_bytes``).
    max_result_bytes: int = MAX_RESULT_BYTES
    #: Hard cap on accumulated spend in USD for one run (None -> unlimited).
    max_cost: float | None = None
    #: Extra attempts for transient assistant-call failures (0 -> no retry).
    retries: int = 0
    #: Observer for swallowed retry attempts:
    #: ``on_retry(message, attempt, max_retries, delay_s)`` before each backoff.
    on_retry: Callable[[AssistantMessage, int, int, float], None] | None = None
    #: Optional artifact spill sink for oversized tool results: called as
    #: ``sink(tool_name, full_text)`` and returns the saved path (or None when
    #: spilling failed). None keeps the plain truncation behavior.
    artifact_sink: Callable[[str, str], Path | None] | None = None


@dataclass
class AgentState:
    """The durable conversation; the loop appends every new message here.

    ``termination`` records *why* the last run ended. It is deliberately not a
    ``StopReason``: that type describes one provider response, while this
    describes the run (e.g. ``"max_turns"``), and the session JSON stays
    unchanged.
    """

    messages: list[Message] = field(default_factory=list)
    termination: str | None = None
    #: Spend attributed to subagents this run (counted by the cost guard but
    #: not by the parent transcript's own usage).
    subagent_spend: float = 0.0


#: Safety net: a model that keeps requesting tools can never spin forever.
_MAX_TURNS = 100

#: How many consecutive length-truncated turns we re-issue before giving up.
_MAX_LENGTH_FAILURES = 2


def _run_cost(messages: list[Message]) -> float:
    """Total spend of the assistant turns produced by this run so far."""
    return sum(
        message.usage.cost.total
        for message in messages
        if isinstance(message, AssistantMessage)
    )


async def agent_loop(
    prompt: str | list[TextContent | ImageContent] | None,
    state: AgentState,
    config: AgentConfig,
    abort: AbortToken | None = None,
) -> AsyncIterator[AgentEvent]:
    """Run the agent loop, yielding ``AgentEvent`` until the run ends.

    A run ends when the assistant stops without asking for tools, its stop
    reason is ``error``/``aborted``, every tool result in a batch asks to
    terminate, or the abort token fires. ``agent_end`` is always emitted and
    carries ``new_messages`` — everything appended to ``state`` this run.
    """
    new_messages: list[Message] = []
    yield AgentEvent(type="agent_start")

    if prompt is not None:
        user = UserMessage(content=prompt, timestamp=int(time.time()))
        state.messages.append(user)
        new_messages.append(user)
        yield AgentEvent(type="message_start", message=user)
        yield AgentEvent(type="message_end", message=user)

    turns = 0
    length_failures = 0
    termination = "stop"
    last_signature: tuple | None = None
    repeat_streak = 0
    guard_injected = False
    tool_stats: dict[str, dict] = {}
    try:
        # Input typed while the previous run was finishing is still queued: pick
        # it up before the first request instead of waiting for a tool round.
        if config.get_steering_messages is not None:
            for message in await config.get_steering_messages():
                state.messages.append(message)
                new_messages.append(message)
                yield AgentEvent(type="message_start", message=message)
                yield AgentEvent(type="message_end", message=message)

        while True:
            if abort is not None and abort.aborted:
                termination = "aborted"
                break
            turn_limit = (
                config.max_turns if config.max_turns is not None else _MAX_TURNS
            )
            if turns >= turn_limit:
                # A pathological model loop, or the caller's explicit cap.
                termination = "max_turns"
                break
            run_spend = _run_cost(new_messages) + state.subagent_spend
            if config.max_cost is not None and run_spend > config.max_cost:
                # Budget already spent: finish here rather than open a new turn,
                # so the transcript keeps every tool call paired with a result.
                termination = "max_cost"
                break
            turns += 1

            # Request context: system prompt prepended to a COPY — the state
            # never sees it. transform_context may rewrite the whole list,
            # again without touching the state.
            request_messages: list[Message] = [
                SystemMessage(content=config.system_prompt, timestamp=int(time.time())),
                *state.messages,
            ]
            if config.transform_context is not None:
                request_messages = list(
                    await config.transform_context(request_messages)
                )

            # "off" is a user-level setting meaning "no thinking"; the wire
            # side never receives the literal string.
            thinking = None if config.thinking in (None, "off") else config.thinking
            stream_fn = (
                config.stream_fn
                if config.stream_fn is not None
                else stream_fn_for(config.model)
            )
            tool_defs = [
                ToolDef(t.name, t.description, t.parameters) for t in config.tools
            ]
            options = StreamOptions(
                api_key=config.api_key,
                thinking=thinking,
                session_id=config.session_id,
                abort=abort,
            )

            yield AgentEvent(type="turn_start")
            final: AssistantMessage | None = None
            saw_message_start = False
            async for event in stream_with_retry(
                stream_fn,
                config.model,
                request_messages,
                tool_defs,
                options,
                retries=config.retries,
                on_retry=config.on_retry,
            ):
                if isinstance(event, RetryAttempt):
                    # The attempt failed before anything was shown; the loop
                    # announces the retry so live frontends can render progress
                    # instead of a silent gap. The failed message never enters
                    # the transcript.
                    yield AgentEvent(type="retry_attempt", retry=event)
                    continue
                if not saw_message_start:
                    saw_message_start = True
                    yield AgentEvent(type="message_start", message=event.partial)
                yield AgentEvent(
                    type="message_update", assistant_event=event, message=event.partial
                )
                if event.type in ("done", "error"):
                    final = event.partial
                    break

            if final is None:  # stream produced no terminal event: stop safely
                termination = "no_stream"
                break

            state.messages.append(final)
            new_messages.append(final)
            yield AgentEvent(type="message_end", message=final)
            yield AgentEvent(type="turn_end")

            if final.stop_reason in ("error", "aborted"):
                termination = final.stop_reason  # provider failure or cancellation
                break

            calls = [
                block
                for block in final.content
                if isinstance(block, ToolCall)
                and not (block.id == "" and block.name == "")
            ]

            if final.stop_reason == "length" and calls:
                # Output hit the token limit: argument fragments may be
                # truncated, and an assistant turn whose tool_calls never get
                # results poisons the next request. Fail every call instead of
                # executing, then let the model re-issue.
                length_failures += 1
                for call in calls:
                    yield AgentEvent(
                        type="tool_execution_start",
                        tool_call_id=call.id,
                        tool_name=call.name,
                        args=call.arguments,
                    )
                outcomes = [truncated_call_outcome(call) for call in calls]
            elif final.stop_reason == "tool_use" and calls:
                length_failures = 0
                for call in calls:
                    yield AgentEvent(
                        type="tool_execution_start",
                        tool_call_id=call.id,
                        tool_name=call.name,
                        args=call.arguments,
                    )
                update_queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue()

                def emit_update(event: AgentEvent, _queue=update_queue) -> None:
                    # loop vars are bound as defaults so the closure cannot
                    # observe a later iteration's queue (B023)
                    _queue.put_nowait(event)

                async def run_batch(
                    _calls=calls, _queue=update_queue, _emit=emit_update
                ) -> list:
                    try:
                        return await run_tool_batch(_calls, config, abort, _emit)
                    finally:
                        _queue.put_nowait(None)  # sentinel: batch settled

                batch_task = asyncio.create_task(run_batch())
                try:
                    while (update := await update_queue.get()) is not None:
                        yield update
                    outcomes = await batch_task
                finally:
                    if not batch_task.done():  # consumer went away mid-batch
                        batch_task.cancel()
                        await asyncio.gather(batch_task, return_exceptions=True)
            else:
                break  # plain end of turn (or a tool_use turn with nothing actionable)

            # Append and announce in assistant source order, not completion
            # order — the transcript must read as a coherent round.
            for outcome in outcomes:
                message = ToolResultMessage(
                    tool_call_id=outcome.call.id,
                    tool_name=outcome.call.name,
                    content=outcome.result.content,
                    is_error=outcome.result.is_error,
                    timestamp=int(time.time()),
                )
                state.messages.append(message)
                new_messages.append(message)
                stats = tool_stats.setdefault(
                    outcome.call.name, {"calls": 0, "errors": 0, "ms": 0}
                )
                stats["calls"] += 1
                stats["errors"] += int(bool(outcome.result.is_error))
                stats["ms"] += outcome.duration_ms
                try:
                    state.subagent_spend += float(
                        (outcome.result.details or {}).get("subagent_cost") or 0.0
                    )
                except (TypeError, ValueError):  # pragma: no cover - defensive
                    pass
                yield AgentEvent(
                    type="tool_execution_end",
                    tool_call_id=outcome.call.id,
                    tool_name=outcome.call.name,
                    args=outcome.call.arguments,
                    result=outcome.result,
                    is_error=outcome.result.is_error,
                    duration_ms=outcome.duration_ms,
                )
                yield AgentEvent(type="message_start", message=message)
                yield AgentEvent(type="message_end", message=message)

            if all(outcome.result.terminate for outcome in outcomes):
                termination = "tool_terminate"
                break
            if length_failures >= _MAX_LENGTH_FAILURES:
                break  # truncation is not resolving: stop instead of re-asking forever

            # Cross-turn loop guard: a model repeating the same call(s) with
            # identical arguments is stuck. Inject one corrective message per
            # run (as a user-role notice) instead of burning max_turns.
            signature = calls_signature(calls)
            poll_like = all(is_poll_call(call) for call in calls)
            if poll_like:
                repeat_streak = 0
            elif signature == last_signature:
                repeat_streak += 1
            else:
                last_signature = signature
                repeat_streak = 1
            if repeat_streak >= config.loop_guard_threshold and not guard_injected:
                guard_injected = True
                notice = UserMessage(
                    content=LOOP_GUARD_TEXT.format(n=repeat_streak),
                    timestamp=int(time.time()),
                )
                # Model-facing only: appended to the request context but NOT to
                # new_messages and with no message events, so it is never
                # persisted, rendered as a user turn, or counted by fork/retry.
                state.messages.append(notice)

            if config.get_steering_messages is not None:
                for message in await config.get_steering_messages():
                    state.messages.append(message)
                    new_messages.append(message)
                    yield AgentEvent(type="message_start", message=message)
                    yield AgentEvent(type="message_end", message=message)
            # else / loop continues: another round with the tool results in context
    finally:
        # The corrective exists only for the turns after it fired: drop it from
        # the durable transcript so resume/retry/fork never see synthetic text.
        state.messages[:] = [
            message for message in state.messages if not is_loop_guard_message(message)
        ]
        state.termination = termination
        yield AgentEvent(
            type="agent_end",
            new_messages=new_messages,
            tool_stats={"tools": tool_stats} if tool_stats else None,
            turns=turns,
            termination=termination,
        )
