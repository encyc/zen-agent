"""Executing tool calls: validation, hooks, timeouts, batching, results.

Extracted from :mod:`zenagent.agent.loop` so the loop reads as a turn machine.
The loop imports this module at top level; the protocol vocabulary this module
needs lives in :mod:`zenagent.agent.protocol`, so the runtime dependency is a
one-way edge loop → tool_exec.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from zenagent.agent.protocol import (
    _MAX_ERROR_TEXT,
    AgentTool,
    ToolContext,
    ToolResultAgent,
    _error_result,
    _hook_accepts,
    _maybe_await,
)
from zenagent.agent.result_budget import apply_result_budget
from zenagent.agent.timeouts import effective_timeout
from zenagent.agent.validate import validate_arguments
from zenagent.events import AbortToken, AgentEvent
from zenagent.types import ToolCall

if TYPE_CHECKING:
    from zenagent.agent.loop import AgentConfig


@dataclass
class ToolOutcome:
    """One executed (or failed) tool call, ready to be appended in order."""

    call: ToolCall
    result: ToolResultAgent
    duration_ms: int


__all__ = [
    "ToolOutcome",
    "execute_tool",
    "is_exclusive",
    "run_tool",
    "run_tool_batch",
    "truncated_call_outcome",
]


async def run_tool(
    tool: AgentTool,
    call: ToolCall,
    arguments: dict,
    abort: AbortToken | None,
    on_update: Callable[[ToolResultAgent], None] | None,
    timeout: float | None,
) -> ToolResultAgent:
    """Execute one tool, forwarding progress and degrading every failure."""
    assert tool.execute is not None
    accepting = True

    def forward(partial: ToolResultAgent) -> None:
        """Deliver progress only while the tool is still running."""
        if accepting and on_update is not None:
            on_update(partial)

    try:
        if timeout is not None:
            return await asyncio.wait_for(
                tool.execute(call.id, arguments, abort, forward), timeout=timeout
            )
        return await tool.execute(call.id, arguments, abort, forward)
    except TimeoutError as exc:
        if timeout is not None:
            return _error_result(
                f"tool {call.name!r} timed out after {timeout:g}s", kind="timeout"
            )
        # The tool raised TimeoutError itself; with no kernel deadline this is a
        # normal tool failure, not a run-abort.
        return _error_result(
            f"{type(exc).__name__}: {exc}"[:_MAX_ERROR_TEXT], kind="tool_error"
        )
    except Exception as exc:  # noqa: BLE001 - degrade, never crash the loop
        return _error_result(f"{type(exc).__name__}: {exc}"[:_MAX_ERROR_TEXT])
    finally:
        accepting = False


async def execute_tool(
    call: ToolCall,
    config: AgentConfig,
    abort: AbortToken | None,
    on_update: Callable[[ToolResultAgent], None] | None = None,
) -> ToolOutcome:
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
        arguments = call.arguments
        timeout = effective_timeout(tool, call.name)
        context = ToolContext(
            call_id=call.id,
            tool=tool,
            cwd=str(Path.cwd()),
            approval=tool.approval,
            timeout_s=timeout,
        )
        verdict = None
        if config.before_tool_call is not None:
            # The resolved tool object accompanies the call so harness hooks can
            # read the tool's own declarations (approval tier, timeout, …)
            # instead of keeping a name list in sync with the tool registry.
            # v0.3-style 2-arg hooks keep working.
            hook = config.before_tool_call
            if _hook_accepts(hook, 4):
                verdict = await _maybe_await(hook(call, arguments, tool, context))
            elif _hook_accepts(hook, 3):
                verdict = await _maybe_await(hook(call, arguments, tool))
            else:
                verdict = await _maybe_await(hook(call, arguments))
        if isinstance(verdict, dict) and verdict.get("block"):
            result = _error_result(
                str(verdict.get("reason", "blocked")), kind="policy"
            )
        elif isinstance(verdict, dict) and isinstance(verdict.get("args"), dict):
            # A hook may normalize arguments (e.g. expand a path). The rewrite is
            # re-validated against the tool schema, and the original ToolCall is
            # left untouched so the transcript keeps what the model actually
            # asked for.
            arguments = verdict["args"]
            problem = validate_arguments(tool.parameters, arguments)
            if problem is not None:
                result = _error_result(
                    f"invalid arguments for tool {call.name}: {problem}",
                    kind="invalid_arguments",
                )
            elif tool.execute is None:
                result = _error_result(
                    f"tool {call.name} has no execute callable", kind="tool_error"
                )
            else:
                result = await run_tool(
                    tool, call, arguments, abort, on_update, timeout
                )
        elif tool.execute is None:
            result = _error_result(
                f"tool {call.name} has no execute callable", kind="tool_error"
            )
        else:
            result = await run_tool(tool, call, arguments, abort, on_update, timeout)

    assert result is not None  # every branch above assigns
    if config.after_tool_call is not None:
        context = ToolContext(
            call_id=call.id,
            tool=tool,
            cwd=str(Path.cwd()),
            approval=tool.approval if tool is not None else "exec",
            timeout_s=effective_timeout(tool, call.name) if tool is not None else None,
        )
        hook = config.after_tool_call
        if _hook_accepts(hook, 3):
            patched = await _maybe_await(hook(call, result, context))
        else:
            patched = await _maybe_await(hook(call, result))
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
    return ToolOutcome(call=call, result=result, duration_ms=duration_ms)


def is_exclusive(tool: AgentTool | None) -> bool:
    """Whether a call must run alone, after the rest of the batch.

    Two declarations mean exclusive: ``execution_mode="sequential"`` and the
    ``write`` approval tier (file mutations). This mirrors omp's
    ``concurrency="exclusive"`` for edit/write without hard-coding tool names.
    """
    if tool is None:
        return False
    return tool.execution_mode == "sequential" or tool.approval == "write"


async def run_tool_batch(
    calls: list[ToolCall],
    config: AgentConfig,
    abort: AbortToken | None,
    emit_update: Callable[[AgentEvent], None] | None = None,
) -> list[ToolOutcome]:
    """Execute a tool batch: parallel tools concurrently, exclusive ones after.

    Returns outcomes in assistant source order regardless of completion order.
    ``emit_update`` receives one ``tool_execution_update`` event per streamed
    partial result, tagged with the call it belongs to.
    """
    tools_by_name = {t.name: t for t in config.tools}
    outcomes: list[ToolOutcome | None] = [None] * len(calls)
    exclusive = [
        i
        for i, call in enumerate(calls)
        if is_exclusive(tools_by_name.get(call.name))
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

        outcomes[index] = await execute_tool(call, config, abort, forward)

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
def truncated_call_outcome(call: ToolCall) -> ToolOutcome:
    """is_error outcome for a call whose response hit the output token limit."""
    return ToolOutcome(
        call=call,
        result=_error_result(
            f"tool call {call.name!r} was not executed: the response hit the "
            "output token limit, so its arguments may be truncated. "
            "Re-issue the call with complete arguments.",
            kind="invalid_arguments",
        ),
        duration_ms=0,
    )


