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
from dataclasses import dataclass
from pathlib import Path

from zenagent.agent.agent import Agent
from zenagent.agent.artifacts import ArtifactSink
from zenagent.agent.loop import AgentTool, ToolResultAgent
from zenagent.agent.subagents import SubagentStore
from zenagent.config import ContextGuard
from zenagent.prompts import tool_description
from zenagent.prompts.registry import read_block
from zenagent.providers.catalog import Model
from zenagent.providers.registry import StreamFn
from zenagent.types import AssistantMessage, TextContent

__all__ = [
    "CHILD_TOOL_NAMES",
    "DEFAULT_TASK_MAX_TURNS",
    "SubagentObserver",
    "TaskTool",
    "build_task_tool",
    "child_tool_names",
    "get_subagent_observer",
    "set_subagent_observer",
]

#: Tools a read-only child may use. An allowlist (not "everything read-tier")
#: keeps stateful/interactive read tools (todo/ask/wait/read_skill) out.
CHILD_TOOL_NAMES = frozenset(
    {"read_file", "list_dir", "glob", "grep", "web_search", "fetch"}
)

DEFAULT_TASK_MAX_TURNS = 12

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


#: The interactive frontend's live hook (the TUI's subagent view).
SubagentObserver = Callable[[str, str, dict], None]

_OBSERVER: SubagentObserver | None = None


def set_subagent_observer(observer: SubagentObserver | None) -> None:
    """Register the live subagent observer (None clears it, like ask_handler)."""
    global _OBSERVER
    _OBSERVER = observer


def get_subagent_observer() -> SubagentObserver | None:
    """The current live observer, or None for headless runs."""
    return _OBSERVER


def _emit(call_id: str, kind: str, payload: dict) -> None:
    observer = _OBSERVER
    if observer is None:
        return
    try:
        observer(call_id, kind, payload)
    except Exception:  # noqa: BLE001 - a UI observer must never break a run
        return


def _progress_text(turn: int, tool: str, args: dict) -> str:
    preview = ""
    for value in args.values():
        if isinstance(value, str) and value.strip():
            preview = " " + value.strip().splitlines()[0][:60]
            break
    return f"turn {turn} · {tool}{preview}"


class _TaskRecorder:
    """Per-call side effects: persistence, card progress, live observer events."""

    #: Structural events the live viewer needs; text arrives at message_end.
    _FORWARDED = frozenset(
        {
            "message_start",
            "message_end",
            "tool_execution_start",
            "tool_execution_update",
            "tool_execution_end",
        }
    )

    #: Minimum seconds between two progress lines pushed to the tool card.
    PROGRESS_INTERVAL = 0.1

    def __init__(self, *, store, call_id, name, model, messages, on_update):
        self._store = store
        self._call_id = call_id
        self._name = name
        self._model = model
        self._messages = messages
        self._on_update = on_update
        self._turn = 0
        self._last_progress = 0.0

    def start(self) -> None:
        self._store.begin(self._call_id, self._name, self._model)
        _emit(self._call_id, "start", {"name": self._name, "model": self._model})

    def on_event(self, event) -> None:
        kind = event.type
        if kind == "message_end":
            self._turn += 1
            self._store.append(self._call_id, self._messages())
        elif kind == "tool_execution_start":
            self._push_progress(event)
            self._store.append(self._call_id, self._messages())
        elif kind == "tool_execution_end":
            self._store.append(self._call_id, self._messages())
        if kind in self._FORWARDED:
            _emit(self._call_id, "event", {"event": event})

    def finish(self, *, status, duration_ms, cost, tokens) -> None:
        self._store.append(self._call_id, self._messages())
        self._store.finish(
            self._call_id,
            status=status,
            duration_ms=duration_ms,
            cost=cost,
            tokens=tokens,
        )
        _emit(
            self._call_id,
            "end",
            {
                "name": self._name,
                "status": status,
                "duration_ms": duration_ms,
                "cost": cost,
                "tokens": tokens,
            },
        )

    def _push_progress(self, event) -> None:
        if self._on_update is None:
            return
        now = time.monotonic()
        if now - self._last_progress < self.PROGRESS_INTERVAL:
            return
        self._last_progress = now
        line = _progress_text(
            max(1, self._turn), str(event.tool_name or "tool"), event.args or {}
        )
        with contextlib.suppress(Exception):
            self._on_update(ToolResultAgent(content=[TextContent(text=line)]))


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
    artifact_sink: ArtifactSink | None = None,
    session_path: Path | None = None,
) -> TaskTool:
    """Build the per-run ``task`` tool (the child uses the parent's model).

    ``model``/``api_key``/``thinking`` may be callables: the child must follow
    the parent Agent's live values, or a ``/model`` switch would keep running
    subagents on the provider selected at bootstrap.
    """
    child_tools = [
        tool for tool in base_tools if tool.name in CHILD_TOOL_NAMES
    ]
    store = SubagentStore(session_path)

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
            system_prompt=read_block("task_child_system"),
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

        recorder = _TaskRecorder(
            store=store,
            call_id=tool_call_id,
            name=name,
            model=live_model.id,
            messages=lambda: child.messages,
            on_update=on_update,
        )
        recorder.start()
        child.subscribe(recorder.on_event)

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
        recorder.finish(
            status=status,
            duration_ms=duration_ms,
            cost=cost,
            tokens=tokens,
        )
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
            },
            "required": ["prompt"],
        },
        execution_mode="sequential",
        approval="read",
        execute=execute,
        bind_session=store.bind_session,
    )
