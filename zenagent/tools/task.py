"""The ``task`` tool: delegate a self-contained, read-only job to a child agent.

Why a child ``Agent`` instead of more tool calls in the parent transcript: the
sub-run's intermediate reads/searches are noise the parent does not need. The
child owns its own transcript and context guard, the parent only receives the
final envelope (plus token/cost accounting).

Deliberately the *minimal* slice of omp's task tool: synchronous single spawn,
read-only children, no worktrees, no async jobs, no structured-output schemas,
no recursion (the child never gets ``task`` back). Those are v0.5+ features.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import time
from collections.abc import Callable, Sequence

from zenagent.agent.agent import Agent
from zenagent.agent.artifacts import ArtifactSink
from zenagent.agent.loop import AgentTool, ToolResultAgent
from zenagent.config import ContextGuard
from zenagent.prompts import tool_description
from zenagent.providers.catalog import Model
from zenagent.providers.registry import StreamFn
from zenagent.types import AssistantMessage, TextContent

__all__ = [
    "CHILD_TOOL_NAMES",
    "DEFAULT_TASK_MAX_TURNS",
    "build_task_tool",
    "child_tool_names",
]

#: Tools a read-only child may use. An allowlist (not "everything read-tier")
#: keeps stateful/interactive read tools (todo/ask/wait/read_skill) out.
CHILD_TOOL_NAMES = frozenset(
    {"read_file", "list_dir", "glob", "grep", "web_search", "fetch"}
)

DEFAULT_TASK_MAX_TURNS = 12

_CHILD_SYSTEM_PROMPT = """You are a read-only research subagent inside a coding agent.
You cannot modify files, run shell commands, or ask the user questions. Work only
on the delegated task and finish with a concise, self-contained answer. Cite
file paths (and line numbers when useful). If the task cannot be completed with
the tools you have, say exactly what is missing instead of guessing."""

_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]")


def child_tool_names(base_tools: Sequence[AgentTool]) -> set[str]:
    """Names of the base tools a read-only child is allowed to see."""
    return {tool.name for tool in base_tools} & CHILD_TOOL_NAMES


def _final_text(messages) -> str:
    for message in reversed(messages):
        if isinstance(message, AssistantMessage):
            return "\n".join(
                block.text
                for block in message.content
                if isinstance(block, TextContent) and block.text
            ).strip()
    return ""


def _child_usage(messages) -> tuple[float, int]:
    cost = 0.0
    tokens = 0
    for message in messages:
        if not isinstance(message, AssistantMessage):
            continue
        usage = message.usage
        tokens += (
            usage.input
            + usage.output
            + usage.cache_read
            + usage.cache_write
        )
        cost += usage.cost.total
    return cost, tokens


def _status_for(child: Agent) -> str:
    termination = child.termination
    if termination == "aborted":
        return "aborted"
    if child.error_message or termination in ("error", "no_stream"):
        return "error"
    if termination in ("max_turns", "max_cost"):
        return "truncated"
    return "ok"


def _envelope(
    name: str,
    status: str,
    duration_ms: int,
    cost: float,
    tokens: int,
    output: str,
    error: str,
) -> str:
    body = (output or error or "(no output)").replace(
        "</task-result>", "&lt;/task-result&gt;"
    )
    return (
        f'<task-result name="{name}" status="{status}" '
        f'duration_ms="{duration_ms}" cost="${cost:.6f}" tokens="{tokens}">\n'
        f"{body}\n</task-result>"
    )


def _live(value):
    """Resolve a static value or a live getter (model switches need the latter)."""
    return value() if callable(value) else value


def build_task_tool(
    *,
    model: Model | Callable[[], Model],
    base_tools: Sequence[AgentTool],
    api_key: str | Callable[[], str] = "",
    stream_fn: StreamFn | None = None,
    thinking: str | Callable[[], str | None] | None = None,
    max_turns: int = DEFAULT_TASK_MAX_TURNS,
    max_cost: float | None = None,
    artifact_sink: ArtifactSink | None = None,
) -> AgentTool:
    """Build the per-run ``task`` tool (the child uses the parent's model).

    ``model``/``api_key``/``thinking`` may be callables: the child must follow
    the parent Agent's live values, or a ``/model`` switch would keep running
    subagents on the provider selected at bootstrap.
    """
    child_tools = [
        tool for tool in base_tools if tool.name in CHILD_TOOL_NAMES
    ]

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
        started = time.monotonic()
        live_model = _live(model)
        live_key = _live(api_key)
        live_thinking = _live(thinking)

        guard = ContextGuard(live_model.context_window)
        child = Agent(
            model=live_model,
            system_prompt=_CHILD_SYSTEM_PROMPT,
            tools=list(child_tools),
            api_key=live_key,
            thinking=live_thinking,
            stream_fn=stream_fn,
            transform_context=guard.transform,
            max_turns=max_turns,
            max_cost=max_cost,
            artifact_sink=artifact_sink,
        )
        child.subscribe(guard.on_event)

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

        failure = ""
        try:
            await child.prompt(f"[task: {name}]\n{prompt}")
        except Exception as exc:  # noqa: BLE001 - a child crash must not kill the parent
            failure = f"{type(exc).__name__}: {exc}"
        finally:
            if abort_task is not None:
                abort_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await abort_task

        duration_ms = round((time.monotonic() - started) * 1000)
        status = _status_for(child)
        if failure and status == "ok":
            status = "error"
        output = _final_text(child.messages)
        cost, tokens = _child_usage(child.messages)
        error = failure or child.error_message or ""
        text = _envelope(name, status, duration_ms, cost, tokens, output, error)
        return ToolResultAgent(
            content=[TextContent(text=text)],
            details={
                "name": name,
                "status": status,
                "duration_ms": duration_ms,
                "cost": cost,
                "tokens": tokens,
                "model": live_model.id,
                "termination": child.termination,
                "error": error,
                # Cost attribution for the parent status/telemetry layer.
                "subagent_cost": cost,
                "subagent_tokens": tokens,
            },
        )

    return AgentTool(
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
            },
            "required": ["prompt"],
        },
        execution_mode="sequential",
        approval="read",
        execute=execute,
    )
