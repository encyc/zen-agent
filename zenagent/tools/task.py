"""The ``task`` tool surface: delegate a self-contained job to a child agent.

Why a child ``Agent`` instead of more tool calls in the parent transcript: the
sub-run's intermediate reads/searches are noise the parent does not need. The
child owns its own transcript and context guard, the parent only receives the
final envelope (plus token/cost accounting).

The machinery behind this tool — child construction, the dispatch runner
(watchdog/recorder/envelope), the background registry, the child tool policy —
lives in :mod:`zenagent.agent.subagents_runtime`. This module is the AgentTool
face: it validates the model's arguments, resolves the per-dispatch live
values, frames the child's prompt, and wraps the runner's result.

Deliberately the *minimal* slice of omp's task tool: synchronous single spawn,
no worktrees, no async jobs, no structured-output schemas, no recursion (the
child never gets ``task`` back). Those are v0.5+ features.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from zenagent.agent.artifacts import ArtifactSink
from zenagent.agent.loop import AgentTool, ToolResultAgent
from zenagent.agent.subagents import SubagentLimits, SubagentStore
from zenagent.agent.subagents_runtime import (
    ObserverSource,
    _envelope,
    _live,
    child_allowed,
    construct_child,
    get_task_manager,
    run_child,
)
from zenagent.config import DEFAULT_TASK_MAX_TURNS, DEFAULT_TASK_TIMEOUT_S
from zenagent.prompts import render, tool_description
from zenagent.prompts.registry import read_block
from zenagent.providers.catalog import Model
from zenagent.providers.registry import StreamFn
from zenagent.types import TextContent

__all__ = [
    "DEFAULT_TASK_MAX_TURNS",
    "TaskTool",
    "build_task_tool",
]

#: ``DEFAULT_TASK_MAX_TURNS`` is re-exported from ``zenagent.config`` and can be
#: overridden per run by ``[task].max_turns``.

_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]")


def _noop_bind(_session_path) -> None:
    return None


@dataclass
class TaskTool(AgentTool):
    """The ``task`` tool plus the session-rebinding hook the runtime uses."""

    bind_session: Callable = _noop_bind


def build_task_tool(
    *,
    model: Model | Callable[[], Model],
    base_tools: Sequence[AgentTool],
    api_key: str | Callable[[], str] = "",
    stream_fn: StreamFn | None = None,
    thinking: str | Callable[[], str | None] | None = None,
    max_turns: int = DEFAULT_TASK_MAX_TURNS,
    max_cost: float | None = None,
    timeout_s: int = DEFAULT_TASK_TIMEOUT_S,
    live_tools: Callable[[], Sequence[AgentTool]] | None = None,
    mode: str | Callable[[], str | None] | None = None,
    resolve_model: Callable[[str], tuple[Model, str]] | None = None,
    artifact_sink: ArtifactSink | None = None,
    session_path: Path | None = None,
    limits: SubagentLimits | None = None,
    before_tool_call: Callable | None = None,
    get_observer: ObserverSource | None = None,
) -> TaskTool:
    """Build the per-run ``task`` tool (the child uses the parent's model).

    ``model``/``api_key``/``thinking`` may be callables: the child must follow
    the parent Agent's live values, or a ``/model`` switch would keep running
    subagents on the provider selected at bootstrap. ``live_tools`` and
    ``mode`` are the same kind of live getter: the child's toolset is resolved
    per dispatch from the parent's current active set and permission mode, so
    MCP refreshes and ``/mode`` switches apply to the next spawn. A ``model``
    argument resolves through ``resolve_model`` (catalog + api key) and, when
    present, overrides the parent's model for that child — thinking follows
    the resolved model's ``reasoning`` flag. ``max_turns``,
    ``timeout_s`` and ``limits`` come from ``[task]`` in config.toml
    (``zenagent.config``); ``timeout_s <= 0`` disables the wall-clock watchdog.
    ``before_tool_call`` is the parent's harness gate: every child tool call
    consults it, so deny patterns and command policy reach subagents too
    (``None`` means no gate). A child has no user channel, so the gate can only
    allow or refuse there — it never prompts.
    """
    store = SubagentStore(session_path, limits)
    #: Current session path (bind_session rebinds it); a background child
    #: pins its own store at dispatch, so later rebinds never touch it.
    session_cell = {"path": session_path}

    def _bind_session(path) -> None:
        session_cell["path"] = path
        store.bind_session(path)

    async def execute(
        tool_call_id: str, args: dict, abort=None, on_update=None
    ) -> ToolResultAgent:
        prompt = str(args.get("prompt") or "").strip()
        if not prompt:
            return ToolResultAgent(
                content=[TextContent(text="prompt is required and must not be empty")],
                is_error=True,
                error_kind="invalid_arguments",
            )
        raw_name = str(args.get("name") or "task").strip() or "task"
        name = _SAFE_NAME.sub("_", raw_name)[:32]
        async_flag = bool(args.get("async"))
        prompt_body = f"[task: {name}]\n{prompt}"
        started = time.monotonic()
        # Freeze at dispatch: live values resolve HERE, once — a background
        # child keeps this toolset/mode/model even if the session moves on.
        live_model = _live(model)
        live_key = _live(api_key)
        live_thinking = _live(thinking)
        model_arg = str(args.get("model") or "").strip()
        if model_arg:
            # Per-dispatch override: fail fast before any child runs, and let
            # thinking follow the resolved model, not the parent session's.
            if resolve_model is None:
                return ToolResultAgent(
                    content=[TextContent(
                        text="the model parameter is not available in this build"
                    )],
                    is_error=True,
                    error_kind="invalid_arguments",
                )
            try:
                live_model, live_key = resolve_model(model_arg)
            except ValueError as exc:
                return ToolResultAgent(
                    content=[TextContent(text=str(exc))],
                    is_error=True,
                    error_kind="invalid_arguments",
                )
            live_thinking = "high" if live_model.reasoning else "off"
        available = _live(live_tools) if live_tools is not None else base_tools
        zen = (_live(mode) or "") == "zen"
        child_tools = [tool for tool in available if child_allowed(tool, zen=zen)]
        child_system_prompt = render(
            read_block("task_child_system"),
            {
                "max_turns": str(max_turns),
                "child_tools": ", ".join(tool.name for tool in child_tools) or "(none)",
            },
        )

        if async_flag:
            if abort is not None and abort.aborted:
                return ToolResultAgent(
                    content=[TextContent(
                        text=_envelope(name, "aborted", 0, 0.0, 0, "", "aborted before start")
                    )],
                    details={
                        "name": name,
                        "status": "aborted",
                        "duration_ms": 0,
                        "cost": 0.0,
                        "tokens": 0,
                        "model": live_model.id,
                    },
                )
            # The dispatch call returns at once, so its abort token never
            # binds the child: background runs are session-scoped, like bash
            # jobs. An already-aborted token refuses the dispatch above.
            child = construct_child(
                model=live_model,
                api_key=live_key,
                thinking=live_thinking,
                stream_fn=stream_fn,
                system_prompt=child_system_prompt,
                child_tools=child_tools,
                max_turns=max_turns,
                max_cost=max_cost,
                artifact_sink=artifact_sink,
                before_tool_call=before_tool_call,
            )
            run = get_task_manager().start(
                tool_call_id,
                name,
                live_model.id,
                lambda: run_child(
                    child=child,
                    prompt_body=prompt_body,
                    name=name,
                    call_id=tool_call_id,
                    store=SubagentStore(session_cell["path"], limits),
                    started=started,
                    timeout_s=timeout_s,
                    on_update=on_update,
                    get_observer=get_observer,
                ),
            )
            text = (
                f"task {run.task_id} dispatched ({name}); call wait with "
                f'job_id="{run.task_id}" to collect the envelope.'
            )
            return ToolResultAgent(
                content=[TextContent(text=text)],
                details={
                    "task_id": run.task_id,
                    "name": name,
                    "dispatched": True,
                    "model": live_model.id,
                },
            )

        child = construct_child(
            model=live_model,
            api_key=live_key,
            thinking=live_thinking,
            stream_fn=stream_fn,
            system_prompt=child_system_prompt,
            child_tools=child_tools,
            max_turns=max_turns,
            max_cost=max_cost,
            artifact_sink=artifact_sink,
            before_tool_call=before_tool_call,
        )

        abort_task: asyncio.Task | None = None
        if abort is not None:
            if abort.aborted:
                return ToolResultAgent(
                    content=[TextContent(
                        text=_envelope(name, "aborted", 0, 0.0, 0, "", "aborted before start")
                    )],
                    details={
                        "name": name,
                        "status": "aborted",
                        "duration_ms": 0,
                        "cost": 0.0,
                        "tokens": 0,
                        "model": live_model.id,
                    },
                )

            async def watch_abort() -> None:
                await abort.wait()
                child.abort()

            abort_task = asyncio.create_task(watch_abort())

        try:
            text, details = await run_child(
                child=child,
                prompt_body=prompt_body,
                name=name,
                call_id=tool_call_id,
                store=store,
                started=started,
                timeout_s=timeout_s,
                on_update=on_update,
                get_observer=get_observer,
            )
        finally:
            if abort_task is not None:
                abort_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await abort_task

        return ToolResultAgent(content=[TextContent(text=text)], details=details)

    return TaskTool(
        name="task",
        description=tool_description("task"),
        parameters={
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Self-contained instructions for the child.",
                },
                "name": {
                    "type": "string",
                    "description": "Short CamelCase label for the child (default task).",
                },
                "async": {
                    "type": "boolean",
                    "description": (
                        "Dispatch in the background and return a task id at once; "
                        "collect the envelope later with the wait tool."
                    ),
                },
                "model": {
                    "type": "string",
                    "description": (
                        "Model for this child (bare id or provider/id); "
                        "defaults to this session's model."
                    ),
                },
            },
            "required": ["prompt"],
        },
        execution_mode="sequential",
        approval="read",
        execute=execute,
        bind_session=_bind_session,
    )
