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
import inspect
import json
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from zenagent.agent.result_budget import apply_result_budget
from zenagent.agent.timeouts import effective_timeout
from zenagent.agent.validate import validate_arguments
from zenagent.events import AbortToken, AgentEvent
from zenagent.providers.catalog import Model
from zenagent.providers.openai_completions import StreamOptions
from zenagent.providers.registry import StreamFn, stream_fn_for
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
    "AgentConfig",
    "AgentState",
    "AgentTool",
    "ToolResultAgent",
    "agent_loop",
]

#: Cap for exception text smuggled into tool results (matches safe_stream).
_MAX_ERROR_TEXT = 500


@dataclass
class ToolResultAgent:
    """What an agent tool returns; ``terminate`` asks the loop to stop.

    ``details`` is structured data for logs and UI rendering only — it is never
    sent to the model (``content`` is the model-facing payload).
    """

    content: list[TextContent | ImageContent]
    details: dict = field(default_factory=dict)
    is_error: bool = False
    terminate: bool = False
    #: Machine-readable failure category ('' for success). The loop fills in
    #: its own kinds (unknown_tool/invalid_arguments/policy/tool_error/timeout);
    #: tools may set a more specific one in the result they return.
    error_kind: str = ""


@dataclass
class AgentTool:
    """A tool the loop may execute on the model's behalf.

    ``execute(tool_call_id, args, abort, on_update)`` receives the abort token
    so it can stop early when the user cancels, and an ``on_update`` callback
    for streaming partial results (ignored by most tools). ``execution_mode=
    "sequential"`` opts the tool out of the concurrent batch: such tools run one
    at a time, in the order the assistant requested them.

    ``approval`` is the tool's own capability declaration consumed by the
    harness permission gate: ``"read"`` (never needs confirmation, allowed in
    read-only modes), ``"write"`` (mutates files/state) or ``"exec"`` (runs
    commands; the default when a tool does not say). The kernel does not
    enforce it — the declaration lets the harness classify tools without a
    hard-coded name list.
    """

    name: str
    description: str
    parameters: dict
    execution_mode: str = "parallel"
    #: Required in practice; None only survives dataclass field ordering and
    #: executing such a tool yields an is_error result.
    execute: (
        Callable[
            [str, dict, AbortToken | None, Callable[[ToolResultAgent], None]],
            Awaitable[ToolResultAgent],
        ]
        | None
    ) = None
    #: Capability tier declared by the tool (``read``/``write``/``exec``).
    approval: str = "exec"
    #: Per-tool wall-clock budget; the loop wraps the call in ``wait_for``.
    #: None means "no kernel deadline" (the tool manages its own, e.g. bash).
    timeout_s: float | None = None


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
    #: Hard cap on accumulated spend in USD for one run (None -> unlimited).
    max_cost: float | None = None
    #: Extra attempts for transient assistant-call failures (0 -> no retry).
    retries: int = 0
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


@dataclass
class _ToolOutcome:
    """One executed (or failed) tool call, ready to be appended in order."""

    call: ToolCall
    result: ToolResultAgent
    duration_ms: int


def _hook_accepts_tool(hook) -> bool:
    """Whether a before hook accepts the resolved tool as a third argument.

    The 3-arg form is the current contract; the 2-arg form was the v0.3
    contract and is still accepted for library users (a TypeError escaping the
    hook would otherwise abort the whole run, outside the tool try/except).
    """
    try:
        signature = inspect.signature(hook)
    except (TypeError, ValueError):  # C callable: assume the current contract
        return True
    try:
        signature.bind(None, {}, None)
    except TypeError:
        return False
    return True


async def _maybe_await(value):
    """Await ``value`` if it is awaitable (sync-or-async hook support)."""
    if inspect.isawaitable(value):
        return await value
    return value


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


def _error_result(text: str, *, kind: str = "tool_error") -> ToolResultAgent:
    return ToolResultAgent(
        content=[TextContent(text=text)], is_error=True, error_kind=kind
    )


def _truncated_call_outcome(call: ToolCall) -> _ToolOutcome:
    """is_error outcome for a call whose response hit the output token limit."""
    return _ToolOutcome(
        call=call,
        result=_error_result(
            f"tool call {call.name!r} was not executed: the response hit the "
            "output token limit, so its arguments may be truncated. "
            "Re-issue the call with complete arguments.",
            kind="invalid_arguments",
        ),
        duration_ms=0,
    )


async def _execute_tool(
    call: ToolCall,
    config: AgentConfig,
    abort: AbortToken | None,
    on_update: Callable[[ToolResultAgent], None] | None = None,
) -> _ToolOutcome:
    """Run one tool call through validation, hooks, and execution.

    Never raises: every failure mode (unknown tool, broken arguments,
    schema-invalid arguments, blocked by ``before_tool_call``, exception from
    ``execute``) degrades to an is_error ``ToolResultAgent`` so the loop keeps
    going. ``execute`` is only ever called with arguments that passed
    :func:`~zenagent.agent.validate.validate_arguments`.
    """
    started = time.monotonic()
    tool = next((t for t in config.tools if t.name == call.name), None)
    result: ToolResultAgent | None = None

    if tool is None:
        result = _error_result(f"unknown tool: {call.name}", kind="unknown_tool")
    elif call.arguments_error is not None:
        result = _error_result(call.arguments_error, kind="invalid_arguments")
    elif not isinstance(call.arguments, dict):
        result = _error_result(
            f"invalid arguments for tool {call.name}: expected a JSON object, "
            f"got {type(call.arguments).__name__}",
            kind="invalid_arguments",
        )
    elif (problem := validate_arguments(tool.parameters, call.arguments)) is not None:
        result = _error_result(
            f"invalid arguments for tool {call.name}: {problem}",
            kind="invalid_arguments",
        )
    else:
        verdict = None
        if config.before_tool_call is not None:
            # The resolved tool object accompanies the call so harness hooks can
            # read the tool's own declarations (approval tier, timeout, …)
            # instead of keeping a name list in sync with the tool registry.
            # v0.3-style 2-arg hooks keep working.
            if _hook_accepts_tool(config.before_tool_call):
                verdict = await _maybe_await(
                    config.before_tool_call(call, call.arguments, tool)
                )
            else:
                verdict = await _maybe_await(
                    config.before_tool_call(call, call.arguments)
                )
        if isinstance(verdict, dict) and verdict.get("block"):
            result = _error_result(
                str(verdict.get("reason", "blocked")), kind="policy"
            )
        elif tool.execute is None:
            result = _error_result(
                f"tool {call.name} has no execute callable", kind="tool_error"
            )
        else:
            accepting = True

            def forward(partial: ToolResultAgent) -> None:
                """Deliver progress only while the tool is still running."""
                if accepting and on_update is not None:
                    on_update(partial)

            timeout = effective_timeout(tool, call.name)
            try:
                if timeout is not None:
                    result = await asyncio.wait_for(
                        tool.execute(call.id, call.arguments, abort, forward),
                        timeout=timeout,
                    )
                else:
                    result = await tool.execute(
                        call.id, call.arguments, abort, forward
                    )
            except TimeoutError as exc:
                if timeout is not None:
                    result = _error_result(
                        f"tool {call.name!r} timed out after {timeout:g}s",
                        kind="timeout",
                    )
                else:
                    # The tool raised TimeoutError itself; with no kernel
                    # deadline this is a normal tool failure, not a run-abort.
                    result = _error_result(
                        f"{type(exc).__name__}: {exc}"[:_MAX_ERROR_TEXT],
                        kind="tool_error",
                    )
            except Exception as exc:  # noqa: BLE001 - degrade, never crash the loop
                result = _error_result(
                    f"{type(exc).__name__}: {exc}"[:_MAX_ERROR_TEXT]
                )
            finally:
                accepting = False

    assert result is not None  # every branch above assigns
    if config.after_tool_call is not None:
        patched = await _maybe_await(config.after_tool_call(call, result))
        if isinstance(patched, ToolResultAgent):
            result = patched
        elif isinstance(patched, dict):
            if "content" in patched:
                result.content = patched["content"]
            if "is_error" in patched:
                result.is_error = patched["is_error"]
            if "terminate" in patched:
                result.terminate = patched["terminate"]

    # Safety net above the per-tool caps: every result — including MCP and
    # extension tools that enforce no cap of their own — gets the same byte
    # budget and truncation notice before it enters the transcript. The spill
    # sink (when configured) saves the full text so the model can read it back.
    result = apply_result_budget(
        result, tool_name=call.name, save_artifact=config.artifact_sink
    )

    duration_ms = round((time.monotonic() - started) * 1000)
    return _ToolOutcome(call=call, result=result, duration_ms=duration_ms)


def _is_exclusive(tool: AgentTool | None) -> bool:
    """Whether a call must run alone, after the rest of the batch.

    Two declarations mean exclusive: ``execution_mode="sequential"`` and the
    ``write`` approval tier (file mutations). This mirrors omp's
    ``concurrency="exclusive"`` for edit/write without hard-coding tool names.
    """
    if tool is None:
        return False
    return tool.execution_mode == "sequential" or tool.approval == "write"


async def _run_tool_batch(
    calls: list[ToolCall],
    config: AgentConfig,
    abort: AbortToken | None,
    emit_update: Callable[[AgentEvent], None] | None = None,
) -> list[_ToolOutcome]:
    """Execute a tool batch: parallel tools concurrently, exclusive ones after.

    Returns outcomes in assistant source order regardless of completion order.
    ``emit_update`` receives one ``tool_execution_update`` event per streamed
    partial result, tagged with the call it belongs to.
    """
    tools_by_name = {t.name: t for t in config.tools}
    outcomes: list[_ToolOutcome | None] = [None] * len(calls)
    exclusive = [
        i
        for i, call in enumerate(calls)
        if _is_exclusive(tools_by_name.get(call.name))
    ]
    parallel = [i for i in range(len(calls)) if i not in exclusive]

    async def run_one(index: int) -> None:
        call = calls[index]

        def forward(partial: ToolResultAgent) -> None:
            if emit_update is not None:
                emit_update(
                    AgentEvent(
                        type="tool_execution_update",
                        tool_call_id=call.id,
                        tool_name=call.name,
                        args=call.arguments,
                        partial_result=partial,
                    )
                )

        outcomes[index] = await _execute_tool(call, config, abort, forward)

    # Two phases: the parallel batch fans out, then exclusive tools run one at
    # a time, in call order. Exclusive tools must not overlap the parallel
    # batch — that is what "exclusive" means (the old implementation ran both
    # tracks concurrently, which was not omp's semantics).
    if parallel:
        await asyncio.gather(*(run_one(i) for i in parallel))
    for index in exclusive:
        await run_one(index)
    return [outcome for outcome in outcomes if outcome is not None]


#: Consecutive identical tool rounds before the loop injects a corrective notice.
_LOOP_GUARD_THRESHOLD = 5

#: Prefix of the synthetic, non-persisted corrective message (the UI filters it).
LOOP_GUARD_PREFIX = "[tool-loop guard]"

_LOOP_GUARD_TEXT = (
    LOOP_GUARD_PREFIX + " The same tool call has repeated {n} times with identical "
    "arguments and produced no new progress. Stop repeating it: change the "
    "arguments, use a different tool, or explain to the user what is blocking you."
)

#: Commands that are legitimate to repeat: they observe a changing state.
_POLL_COMMAND = re.compile(r"^\s*(sleep|wait|tail|ps|pgrep|top|jobs)\b")


def is_loop_guard_message(message: object) -> bool:
    """Whether ``message`` is the synthetic loop-guard corrective."""
    return isinstance(message, UserMessage) and str(
        getattr(message, "content", "")
    ).startswith(LOOP_GUARD_PREFIX)


def _is_poll_call(call: ToolCall) -> bool:
    """Whether this call is a pure wait/observe poll (exempt from the guard)."""
    if call.name == "wait":
        # Waiting for a background job is exactly the legitimate long poll.
        return True
    if call.name != "bash":
        return False
    return bool(_POLL_COMMAND.match(str(call.arguments.get("command", ""))))


def _calls_signature(calls: list[ToolCall]) -> tuple:
    """Stable signature of a tool batch, for detecting identical repeats."""
    return tuple(
        sorted(
            (call.name, json.dumps(call.arguments, sort_keys=True, default=str))
            for call in calls
        )
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
            turn_limit = config.max_turns if config.max_turns is not None else _MAX_TURNS
            if turns >= turn_limit:
                # A pathological model loop, or the caller's explicit cap.
                termination = "max_turns"
                break
            if config.max_cost is not None and _run_cost(new_messages) > config.max_cost:
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
                request_messages = list(await config.transform_context(request_messages))

            # "off" is a user-level setting meaning "no thinking"; the wire
            # side never receives the literal string.
            thinking = None if config.thinking in (None, "off") else config.thinking
            stream_fn = config.stream_fn if config.stream_fn is not None else stream_fn_for(config.model)
            tool_defs = [ToolDef(t.name, t.description, t.parameters) for t in config.tools]
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
            ):
                if not saw_message_start:
                    saw_message_start = True
                    yield AgentEvent(type="message_start", message=event.partial)
                yield AgentEvent(type="message_update", assistant_event=event, message=event.partial)
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
                if isinstance(block, ToolCall) and not (block.id == "" and block.name == "")
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
                outcomes = [_truncated_call_outcome(call) for call in calls]
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
                ) -> list[_ToolOutcome]:
                    try:
                        return await _run_tool_batch(_calls, config, abort, _emit)
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
            signature = _calls_signature(calls)
            poll_like = all(_is_poll_call(call) for call in calls)
            if poll_like:
                repeat_streak = 0
            elif signature == last_signature:
                repeat_streak += 1
            else:
                last_signature = signature
                repeat_streak = 1
            if repeat_streak >= _LOOP_GUARD_THRESHOLD and not guard_injected:
                guard_injected = True
                notice = UserMessage(
                    content=_LOOP_GUARD_TEXT.format(n=repeat_streak),
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
            message
            for message in state.messages
            if not is_loop_guard_message(message)
        ]
        state.termination = termination
        yield AgentEvent(
            type="agent_end",
            new_messages=new_messages,
            tool_stats={"tools": tool_stats} if tool_stats else None,
        )
