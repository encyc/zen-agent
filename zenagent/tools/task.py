"""The ``task`` tool: delegate a self-contained job to a child agent.

Why a child ``Agent`` instead of more tool calls in the parent transcript: the
sub-run's intermediate reads/searches are noise the parent does not need. The
child owns its own transcript and context guard, the parent only receives the
final envelope (plus token/cost accounting).

The child's capability ceiling is the parent's current permission mode: in zen
it sees the parent's active tools minus a small denylist; in every other mode
it is read-only. Identity is never inherited — the child gets a dedicated
system prompt and reports to the parent; it cannot reach the user.

Deliberately the *minimal* slice of omp's task tool: synchronous single spawn,
no worktrees, no async jobs, no structured-output schemas, no recursion (the
child never gets ``task`` back). Those are v0.5+ features.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from itertools import count
from pathlib import Path

from zenagent.agent.agent import Agent
from zenagent.agent.artifacts import ArtifactSink
from zenagent.agent.loop import AgentTool, ToolResultAgent
from zenagent.agent.subagents import SubagentLimits, SubagentStore
from zenagent.config import DEFAULT_TASK_MAX_TURNS, DEFAULT_TASK_TIMEOUT_S, ContextGuard
from zenagent.prompts import render, tool_description
from zenagent.prompts.registry import read_block
from zenagent.providers.catalog import Model
from zenagent.providers.registry import StreamFn
from zenagent.types import AssistantMessage, TextContent, ToolCall

__all__ = [
    "TASK_RUN_RETENTION",
    "DEFAULT_TASK_MAX_TURNS",
    "SubagentObserver",
    "TaskManager",
    "TaskRun",
    "TaskTool",
    "UnknownTaskError",
    "build_task_tool",
    "child_allowed",
    "child_tool_names",
    "close_task_manager",
    "get_subagent_observer",
    "get_task_manager",
    "set_subagent_observer",
]

#: Tools a child may never see, whatever the parent's mode: ``task`` (no
#: recursion), ``ask`` (the child has no user channel), ``wait`` (the parent
#: owns background jobs), ``todo`` and the ``memory`` family (shared session
#: state the child must not write behind the parent's back), ``checkpoint``/
#: ``rewind`` (time travel over the parent's transcript is the parent's call).
_CHILD_TOOL_DENYLIST = frozenset({"task", "ask", "wait", "todo", "rewind"})
_CHILD_TOOL_DENY_PREFIXES = ("memory", "checkpoint")


def child_allowed(tool: AgentTool, *, zen: bool) -> bool:
    """Whether a child may see ``tool`` under the parent's permission mode.

    Zen parents delegate their full active set (minus the denylist); every
    other mode delegates read tools only. The child is not a second user: it
    never exceeds what the session it was born in allows.
    """
    if tool.name in _CHILD_TOOL_DENYLIST:
        return False
    if tool.name.startswith(_CHILD_TOOL_DENY_PREFIXES):
        return False
    return zen or tool.approval == "read"


def child_tool_names(base_tools: Sequence[AgentTool], *, zen: bool = False) -> set[str]:
    """Names of the tools a child may see from ``base_tools`` under ``zen``."""
    return {tool.name for tool in base_tools if child_allowed(tool, zen=zen)}


#: ``DEFAULT_TASK_MAX_TURNS`` is re-exported from ``zenagent.config`` and can be
#: overridden per run by ``[task].max_turns``; it stays in ``__all__`` below.

_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]")


def _final_text(messages) -> str:
    # The last assistant message may be textless — a tool call cut off by the
    # turn/timeout budget. Fall back to the newest earlier text (often a
    # checkpoint the child wrote for exactly this case) so a truncated run
    # still returns something usable instead of "(no output)".
    for message in reversed(messages):
        if not isinstance(message, AssistantMessage):
            continue
        text = "\n".join(
            block.text
            for block in message.content
            if isinstance(block, TextContent) and block.text
        ).strip()
        if text:
            return text
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


#: Tools whose ``path`` argument means "this file's content changed on disk".
#: bash-side writes are invisible here by design: shell commands are not
#: statically attributable to files (a known, documented limitation).
_WRITING_TOOLS = ("write_file", "edit_file")


def _files_touched(messages) -> list[str]:
    """Paths the child wrote, deduplicated in first-touched order.

    Extracted from the child's own tool calls — not self-reported — so the
    manifest stays honest even when the summary text embellishes.
    """
    seen: dict[str, None] = {}
    for message in messages:
        if not isinstance(message, AssistantMessage):
            continue
        for block in message.content:
            if (
                isinstance(block, ToolCall)
                and block.name in _WRITING_TOOLS
            ):
                path = (block.arguments or {}).get("path")
                if isinstance(path, str) and path:
                    seen.setdefault(path)
    return list(seen)


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
    *,
    reason: str = "",
    turns: int = 0,
) -> str:
    body = (output or error or "(no output)").replace(
        "</task-result>", "&lt;/task-result&gt;"
    )
    if reason:
        # The parent model only sees this envelope: say *why* the run stopped,
        # or it invents an explanation (e.g. "the child blew its context").
        body += (
            f"\n\n(partial output: the child stopped at its {reason} budget "
            f"after {turns} turns; narrow the task and delegate again to continue.)"
        )
    attrs = f'name="{name}" status="{status}"'
    if reason:
        attrs += f' reason="{reason}" turns="{turns}"'
    return (
        f"<task-result {attrs} "
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


class UnknownTaskError(KeyError):
    """A wait named a task id the manager does not know."""

    def __init__(self, task_id: str, known: list[str]) -> None:
        super().__init__(task_id)
        self.task_id = task_id
        #: Ids still known to the manager, in start order.
        self.known = known

    def __str__(self) -> str:
        return f"unknown task id {self.task_id!r}"


#: Completed runs retained per manager; older ones are pruned on completion.
TASK_RUN_RETENTION = 32


@dataclass
class TaskRun:
    """One background child known to the TaskManager.

    ``status`` is ``"running"`` until the monitor records the outcome; the
    envelope contract is the same tuple the sync path builds
    (``_run_child`` returns ``(envelope_text, details)``).
    """

    task_id: str
    call_id: str
    name: str
    model_id: str
    started_at: float
    status: str = "running"
    envelope: str = ""
    details: dict = field(default_factory=dict)
    _done: asyncio.Event = field(default_factory=asyncio.Event)
    _task: asyncio.Task | None = None


class TaskManager:
    """Registry of background child runs (mirrors ``jobs.JobManager``'s shape).

    Deliberately a parallel implementation, not a subclass: collection is
    retention-based — a completed envelope stays waitable until retention
    prunes it — where jobs are cleanup-on-wait, and there is no process
    group to kill. Runs belong to the loop that spawned them; a new loop
    abandons the old manager's runs (cancel best-effort, then drop).
    """

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._runs: dict[str, TaskRun] = {}
        self._retired: deque[str] = deque()
        self._counter = count(1)

    # -- lifecycle ---------------------------------------------------------

    def start(self, call_id, name, model_id, coro_factory) -> TaskRun:
        """Register one run and spawn ``coro_factory()`` as a background task."""
        self._loop = asyncio.get_running_loop()
        task_id = f"task_{next(self._counter)}"
        run = TaskRun(
            task_id=task_id,
            call_id=call_id,
            name=name,
            model_id=model_id,
            started_at=time.time(),
        )
        self._runs[task_id] = run
        run._task = asyncio.create_task(self._monitor(run, coro_factory()))
        return run

    async def wait(self, task_id: str, timeout_s: float = 120.0) -> TaskRun:
        """Block until ``task_id`` finishes (or the timeout), idempotently.

        A timeout returns the still-running run without killing it. Raises
        :class:`UnknownTaskError` for an id the manager does not know.
        """
        self._loop = asyncio.get_running_loop()
        run = self._runs.get(task_id)
        if run is None:
            raise UnknownTaskError(task_id, self.ids())
        if run.status == "running":
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(
                    run._done.wait(), timeout=max(float(timeout_s), 0.0)
                )
        return run

    async def close(self) -> None:
        """Cancel every in-flight run and drop the registry (shutdown/tests)."""
        runs = list(self._runs.values())
        self._runs.clear()
        for run in runs:
            if run._task is not None and not run._task.done():
                run._task.cancel()
        for run in runs:
            if run._task is not None:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await run._task

    def _abandon(self) -> None:
        """The loop is going away: cancel in-flight tasks, report the truth."""
        for run in self._runs.values():
            if run._task is not None and not run._task.done():
                run._task.cancel()
            if run.status == "running":
                run.status = "aborted"
                run._done.set()
        self._runs.clear()

    # -- introspection -----------------------------------------------------

    def running_call_ids(self) -> set[str]:
        """Call ids of runs still in flight (carry_task_dir skips these)."""
        return {
            run.call_id for run in self._runs.values() if run.status == "running"
        }

    def ids(self) -> list[str]:
        return list(self._runs)

    # -- internals ---------------------------------------------------------

    async def _monitor(self, run: TaskRun, coro) -> None:
        try:
            envelope, details = await coro
        except asyncio.CancelledError:
            run.status = "aborted"
        except Exception as exc:  # noqa: BLE001 - a child crash must not kill the manager
            run.status = "error"
            run.envelope = f"{type(exc).__name__}: {exc}"
        else:
            run.status = "ok"
            run.envelope = envelope
            run.details = details
        run._done.set()
        self._retire(run.task_id)

    def _retire(self, task_id: str) -> None:
        self._retired.append(task_id)
        while len(self._retired) > TASK_RUN_RETENTION:
            victim = self._retired.popleft()
            self._runs.pop(victim, None)


#: The interactive frontend shares this module-level manager with ``wait``;
#: like :func:`zenagent.tools.jobs.get_job_manager`, a changed event loop
#: abandons the previous manager instead of leaking its tasks.
_task_manager: TaskManager | None = None


def get_task_manager() -> TaskManager:
    """Process-wide task manager bound to the current event loop."""
    global _task_manager
    loop = asyncio.get_running_loop()
    if _task_manager is None or _task_manager._loop is not loop:
        if _task_manager is not None:
            _task_manager._abandon()
        _task_manager = TaskManager()
        _task_manager._loop = loop
    return _task_manager


async def close_task_manager() -> None:
    """Close and forget the current manager (shutdown, test teardown)."""
    global _task_manager
    if _task_manager is not None:
        await _task_manager.close()
        _task_manager = None


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
    timeout_s: int = DEFAULT_TASK_TIMEOUT_S,
    live_tools: Callable[[], Sequence[AgentTool]] | None = None,
    mode: str | Callable[[], str | None] | None = None,
    resolve_model: Callable[[str], tuple[Model, str]] | None = None,
    artifact_sink: ArtifactSink | None = None,
    session_path: Path | None = None,
    limits: SubagentLimits | None = None,
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
    """
    store = SubagentStore(session_path, limits)
    #: Current session path (bind_session rebinds it); a background child
    #: pins its own store at dispatch, so later rebinds never touch it.
    session_cell = {"path": session_path}

    def _bind_session(path) -> None:
        session_cell["path"] = path
        store.bind_session(path)

    async def _run_child(
        *,
        child: Agent,
        prompt_body: str,
        name: str,
        call_id: str,
        store: SubagentStore,
        started: float,
        timeout_s: int,
        on_update,
    ) -> tuple[str, dict]:
        """Shared dispatch body: watchdog, recorder, prompt, envelope.

        Returns ``(envelope_text, details)`` — the same tuple the TaskManager
        stores for background runs.
        """
        # Wall-clock watchdog: the loop-level tool timeout would cancel this
        # coroutine and drop the transcript's final status, so the budget is
        # enforced child-side — abort() lets the child wind down and still
        # write its end line. A loose fuse against runaway no-output runs.
        timed_out = False
        timeout_task: asyncio.Task | None = None
        if timeout_s and timeout_s > 0:

            async def watch_timeout() -> None:
                nonlocal timed_out
                await asyncio.sleep(timeout_s)
                timed_out = True
                child.abort()

            timeout_task = asyncio.create_task(watch_timeout())

        recorder = _TaskRecorder(
            store=store,
            call_id=call_id,
            name=name,
            model=child.model.id,
            messages=lambda: child.messages,
            on_update=on_update,
        )
        recorder.start()
        child.subscribe(recorder.on_event)

        failure = ""
        try:
            await child.prompt(prompt_body)
        except Exception as exc:  # noqa: BLE001 - a child crash must not kill the parent
            failure = f"{type(exc).__name__}: {exc}"
        finally:
            if timeout_task is not None:
                timeout_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await timeout_task

        duration_ms = round((time.monotonic() - started) * 1000)
        status = _status_for(child)
        if timed_out and status == "aborted":
            # The watchdog fired, not the user: a budget outcome, not an
            # interrupt. The envelope must say which budget and why.
            status = "truncated"
        if failure and status == "ok":
            status = "error"
        output = _final_text(child.messages)
        cost, tokens = _child_usage(child.messages)
        error = failure or child.error_message or ""
        termination = child.termination or ""
        reason = ""
        if status == "truncated":
            reason = "timeout" if timed_out else termination
        turns = sum(
            1 for message in child.messages if isinstance(message, AssistantMessage)
        )
        text = _envelope(
            name,
            status,
            duration_ms,
            cost,
            tokens,
            output,
            error,
            reason=reason,
            turns=turns,
        )
        recorder.finish(
            status=status,
            duration_ms=duration_ms,
            cost=cost,
            tokens=tokens,
        )
        details = {
            "name": name,
            "status": status,
            "duration_ms": duration_ms,
            "cost": cost,
            "tokens": tokens,
            "model": child.model.id,
            "termination": child.termination,
            "reason": reason,
            "turns": turns,
            "error": error,
            "files_touched": _files_touched(child.messages),
            # Cost attribution for the parent status/telemetry layer.
            "subagent_cost": cost,
            "subagent_tokens": tokens,
        }
        return text, details

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

        def _construct() -> Agent:
            guard = ContextGuard(live_model.context_window)
            child = Agent(
                model=live_model,
                system_prompt=child_system_prompt,
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
            return child

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
            child = _construct()
            run = get_task_manager().start(
                tool_call_id,
                name,
                live_model.id,
                lambda: _run_child(
                    child=child,
                    prompt_body=prompt_body,
                    name=name,
                    call_id=tool_call_id,
                    store=SubagentStore(session_cell["path"], limits),
                    started=started,
                    timeout_s=timeout_s,
                    on_update=on_update,
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

        child = _construct()

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
            text, details = await _run_child(
                child=child,
                prompt_body=prompt_body,
                name=name,
                call_id=tool_call_id,
                store=store,
                started=started,
                timeout_s=timeout_s,
                on_update=on_update,
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
