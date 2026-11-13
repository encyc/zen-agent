"""The subagent runtime: child construction, dispatch, and the manager.

Homes everything the ``task`` tool delegates to: the child ``Agent``
construction (capability ceiling, context guard), the synchronous dispatch
runner (watchdog, transcript recorder, envelope accounting), the background
:class:`TaskManager` registry that ``wait`` reads, and the child-tool policy.

Lives under ``agent/`` because it is runtime machinery that happens to be
reached through a tool — the 2026-11-07 review called the tool module "a
subagent runtime wearing a tool costume". The tool surface (schema, argument
validation, prompt framing) stays in :mod:`zenagent.tools.task`.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from itertools import count

from zenagent.agent.agent import Agent
from zenagent.agent.artifacts import ArtifactSink
from zenagent.agent.loop import AgentTool, ToolResultAgent
from zenagent.agent.subagents import SubagentStore
from zenagent.config import ContextGuard
from zenagent.providers.catalog import Model
from zenagent.providers.registry import StreamFn
from zenagent.types import AssistantMessage, TextContent, ToolCall

__all__ = [
    "TASK_RUN_RETENTION",
    "ObserverSource",
    "SubagentObserver",
    "TaskManager",
    "TaskRun",
    "UnknownTaskError",
    "child_allowed",
    "child_tool_names",
    "close_task_manager",
    "construct_child",
    "get_task_manager",
    "run_child",
]


#: Tools a child may never see, whatever the parent's mode: ``task`` (no
#: recursion), ``ask`` (the child has no user channel), ``wait`` (the parent
#: owns background jobs), ``todo`` and the ``memory`` family (shared session
#: state the child must not write behind the parent's back), ``checkpoint``/
#: ``rewind`` (time travel over the parent's transcript is the parent's call).
_CHILD_TOOL_DENYLIST = frozenset({"task", "ask", "wait", "todo", "rewind"})
_CHILD_TOOL_DENY_PREFIXES = ("memory", "checkpoint")


def child_allowed(tool: AgentTool, *, auto: bool) -> bool:
    """Whether a child may see ``tool`` under the parent's permission mode.

    Auto parents delegate their full active set (minus the denylist); every
    other mode delegates read tools only. The child is not a second user: it
    never exceeds what the session it was born in allows.
    """
    if tool.name in _CHILD_TOOL_DENYLIST:
        return False
    if tool.name.startswith(_CHILD_TOOL_DENY_PREFIXES):
        return False
    return auto or tool.approval == "read"


def child_tool_names(base_tools: Sequence[AgentTool], *, auto: bool = False) -> set[str]:
    """Names of the tools a child may see from ``base_tools`` under ``auto``."""
    return {tool.name for tool in base_tools if child_allowed(tool, auto=auto)}


def _live(value):
    """Resolve a static value or a live getter (model switches need the latter)."""
    return value() if callable(value) else value


#: The interactive frontend's live hook (the TUI's subagent view).
SubagentObserver = Callable[[str, str, dict], None]

#: Late binding: the observer is installed on the Runtime after bootstrap, so
#: the recorder resolves it at emit time.
ObserverSource = Callable[[], SubagentObserver | None]


def _emit(
    get_observer: ObserverSource, call_id: str, kind: str, payload: dict
) -> None:
    observer = get_observer()
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
    #: Spend keys (subagent_cost/subagent_tokens) are forwarded to the hooks
    #: on the FIRST collection only; a re-wait re-reads the envelope text but
    #: must not double-count the run into the HUD or the parent's max_cost.
    spend_reported: bool = False
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
            run.details = {"status": "error"}
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

    def __init__(self, *, store, call_id, name, model, messages, on_update, get_observer):
        self._store = store
        self._call_id = call_id
        self._name = name
        self._model = model
        self._messages = messages
        self._on_update = on_update
        self._get_observer = get_observer
        self._turn = 0
        self._last_progress = 0.0

    def start(self) -> None:
        self._store.begin(self._call_id, self._name, self._model)
        _emit(
            self._get_observer,
            self._call_id,
            "start",
            {"name": self._name, "model": self._model},
        )

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
            _emit(self._get_observer, self._call_id, "event", {"event": event})

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
            self._get_observer,
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


async def run_child(
    *,
    child: Agent,
    prompt_body: str,
    name: str,
    call_id: str,
    store: SubagentStore,
    started: float,
    timeout_s: int,
    on_update,
    get_observer: ObserverSource | None = None,
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
        get_observer=get_observer or (lambda: None),
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


def construct_child(
    *,
    model: Model,
    api_key: str,
    thinking: str | None,
    stream_fn: StreamFn | None,
    system_prompt: str,
    child_tools: list[AgentTool],
    max_turns: int,
    max_cost: float | None,
    artifact_sink: ArtifactSink | None,
    before_tool_call: Callable | None,
) -> Agent:
    guard = ContextGuard(model.context_window)
    child = Agent(
                model=model,
                system_prompt=system_prompt,
                tools=list(child_tools),
                api_key=api_key,
                thinking=thinking,
                stream_fn=stream_fn,
                transform_context=guard.transform,
                max_turns=max_turns,
                max_cost=max_cost,
                artifact_sink=artifact_sink,
                before_tool_call=before_tool_call,
            )
    child.subscribe(guard.on_event)
    return child

