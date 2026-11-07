"""The tool protocol: the vocabulary shared by the loop and its executor.

Homes the extension surface every tool, MCP bridge, skill, and extension
imports (:class:`AgentTool`/:class:`ToolResultAgent`/:class:`ToolContext`)
plus the small helpers the executor reuses. Living outside
:mod:`zenagent.agent.loop` keeps changes to the loop module from rippling
through the whole extension surface.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from zenagent.events import AbortToken
from zenagent.types import ImageContent, TextContent

__all__ = [
    "AgentTool",
    "ToolContext",
    "ToolResultAgent",
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
    #: Declared but never exposed: the tool exists in the assembly layer (so a
    #: frontend may toggle it later) yet the model must not see or call it.
    hidden: bool = False
    #: Exposed only after the user activates it (settings or a frontend
    #: toggle); the registry starts it out of the active set.
    default_inactive: bool = False


@dataclass(frozen=True)
class ToolContext:
    """What a tool-call hook may inspect about the call being executed.

    Passed to 4-argument ``before_tool_call`` hooks and 3-argument
    ``after_tool_call`` hooks. It carries the resolved tool declarations plus
    the process cwd the tool will actually run against, so a harness hook never
    has to re-derive policy from a name list.
    """

    call_id: str
    #: None only when the model called a tool that is not registered.
    tool: AgentTool | None
    cwd: str
    approval: str
    timeout_s: float | None


def _error_result(text: str, *, kind: str = "tool_error") -> ToolResultAgent:
    return ToolResultAgent(
        content=[TextContent(text=text)], is_error=True, error_kind=kind
    )


def _hook_accepts(hook, arity: int) -> bool:
    """Whether ``hook`` can be called with ``arity`` positional arguments.

    Hooks evolved: v0.3 ``before_tool_call(call, args)``, v0.4 added the
    resolved tool as a third argument, and the current contract also passes a
    :class:`ToolContext`. A ``TypeError`` escaping the hook would abort the run
    outside the tool try/except, so the executor probes the signature instead.
    """
    try:
        signature = inspect.signature(hook)
    except (TypeError, ValueError):  # C callable: assume the current contract
        return arity == 4
    try:
        signature.bind(*(None for _ in range(arity)))
    except TypeError:
        return False
    return True


def _hook_accepts_tool(hook) -> bool:
    """Whether a before hook accepts the resolved tool as a third argument."""
    return _hook_accepts(hook, 3)


async def _maybe_await(value):
    """Await ``value`` if it is awaitable (sync-or-async hook support)."""
    if inspect.isawaitable(value):
        return await value
    return value
