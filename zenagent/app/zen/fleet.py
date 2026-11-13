"""The zen fleet: one router's workers, dispatched asynchronously.

A fleet belongs to one project (one RuntimeSlot). Workers are the project's
other sessions; dispatching builds each worker a full runtime via
:meth:`zenagent.app.bootstrap.prepare` resumed onto that session, runs the task
prompt, and injects the final assistant text back into the router conversation
through ``chat.steer`` — the dsh rule that results arrive on their own, never
waited for. A semaphore caps concurrently running workers; excess dispatches
queue.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from zenagent.agent.session import Session, read_session_titles, unique_session_path
from zenagent.app.agent_profile import AgentProfile, Supervisor
from zenagent.app.bootstrap import prepare
from zenagent.app.records import open_session
from zenagent.app.run_wiring import update_run_scope
from zenagent.app.runtime import RuntimeOptions
from zenagent.app.zen.prompts import worker_prompt
from zenagent.app.zen.roster import read_roster
from zenagent.app.zen.roster import set_focus as roster_set_focus
from zenagent.types import AssistantMessage, TextContent, UserMessage

if TYPE_CHECKING:
    from zenagent.app.chat import ChatController

__all__ = ["REPORT_LIMIT", "RosterRow", "WorkerHandle", "ZenFleet", "worker_statuses"]

_log = logging.getLogger(__name__)


def _supervisor_key(handle: WorkerHandle) -> str:
    return f"zen:{Path(handle.path).stem}"

#: Worker states the roster reports. ``router`` rows carry ``idle`` status and
#: the ``router`` role instead.
worker_statuses = ("idle", "queued", "working", "waiting", "blocked", "failed")

#: A worker report is capped so the router conversation stays readable.
REPORT_LIMIT = 800

RosterRow = dict[str, str]

def _clip(text: str, limit: int = 48) -> str:
    """One-line display summary for a worker whose focus is not set."""
    collapsed = " ".join(text.split()).strip(" #>*`-")
    if collapsed == "":
        return ""
    return collapsed if len(collapsed) <= limit else f"{collapsed[: limit - 1].rstrip()}…"


def _session_summary(path: Path) -> str:
    """The first user task in a worker session, without control framing.

    Workers created by ``fleet_spawn`` have no focus until the router sets
    one; a timestamp stem is not a name a person can tell apart. The injected
    worker prompt starts with ``[zen 派工]``, so the task line is the useful
    part when focus is empty.
    """
    try:
        messages = Session(path).load()
    except Exception:  # noqa: BLE001 - naming must never break the roster
        return ""
    for message in messages:
        if not isinstance(message, UserMessage):
            continue
        if isinstance(message.content, str):
            text = message.content
        else:
            text = "".join(
                getattr(block, "text", "")
                for block in message.content
                if isinstance(block, TextContent)
            )
        if text.startswith("[zen 派工]"):
            for line in text.splitlines():
                if line.startswith("任务："):
                    text = line.removeprefix("任务：")
                    break
        summary = _clip(text)
        if summary:
            return summary
    return ""




class WorkerHandle:
    """One worker session's live dispatch state."""

    def __init__(self, path: str, title: str, focus: str) -> None:
        self.path = path
        self.title = title
        self.focus = focus
        self.status: str = "idle"
        self.last_report: str = ""
        self.agent: Any = None
        self.runtime: Any = None
        self.stopped = False
        self.task: asyncio.Task | None = None


class ZenFleet:
    """Async dispatch of worker sessions on behalf of one router session."""

    def __init__(
        self,
        *,
        chat: ChatController | None,
        root: Path,
        sessions_dir: Path,
        router_path: str,
        max_concurrent: int = 3,
        on_change: Callable[[], None] | None = None,
        confirm: Callable | None = None,
        mode: str | None = None,
        batch_window_s: float = 0.5,
        run_timeout_s: float = 0,
        report_limit: int = REPORT_LIMIT,
        worker_mode: str = "inherit",
        supervisor: Supervisor | None = None,
    ) -> None:
        self.chat = chat
        self.root = Path(root)
        self.sessions_dir = Path(sessions_dir)
        self.router_path = str(router_path)
        self.max_concurrent = max(1, int(max_concurrent))
        self.on_change = on_change
        #: The owning slot's confirm callback: a worker's mutating tool calls
        #: surface as approvals in the UI, tagged with the router's frames.
        self.confirm = confirm
        #: The router's permission mode. Workers inherit it except for auto:
        #: the auto bypass approves mutations, so workers downgrade to ask
        #: at the build boundary and their mutating tools surface as approvals.
        self.mode = mode
        self._handles: dict[str, WorkerHandle] = {}
        #: Reports completing within this window reach the router as ONE
        #: message (dsh batching): one run reviews the whole burst instead of
        #: one run per report. 0 disables the window (same-tick still merges).
        self.batch_window_s = max(0.0, float(batch_window_s))
        #: Wall-clock budget for one worker run; ``0`` disables the watchdog.
        self.run_timeout_s = max(0.0, float(run_timeout_s))
        #: One report is cut to this many characters before it reaches the router.
        self.report_limit = max(1, int(report_limit))
        #: ``inherit`` keeps the router's mode but downgrades auto to ask (the
        #: historical behaviour); a concrete mode pins the worker's broker.
        self.worker_mode = worker_mode
        self.supervisor = supervisor
        self._report_buffer: list[str] = []
        self._wake_task: asyncio.Task | None = None
        self._sem = asyncio.Semaphore(self.max_concurrent)
        self._closing = False

    # -- queries -------------------------------------------------------------

    def status_of(self, session_path: str) -> str:
        """The live status of one worker session (``idle`` when untouched)."""
        handle = self._handles.get(str(session_path))
        return handle.status if handle is not None else "idle"

    def has_active(self) -> bool:
        """True while any worker is working or queued (pins the slot)."""
        return any(h.status in ("working", "queued") for h in self._handles.values())

    def roster(self) -> list[RosterRow]:
        """Every session of the project as a roster row, router first."""
        entries = read_roster(self.sessions_dir)
        try:
            titles = read_session_titles(self.sessions_dir)
        except OSError:
            titles = {}
        tracked = set(entries) | {Path(self.router_path).stem}
        rows: list[RosterRow] = []
        seen: set[str] = set()
        for file in sorted(self.sessions_dir.glob("*.jsonl")):
            stem = file.stem
            if stem not in tracked:
                continue
            path = str(file)
            seen.add(path)
            role = "router" if path == self.router_path else entries.get(stem).role
            handle = self._handles.get(path)
            focus = (
                handle.focus
                if handle is not None and handle.focus
                else entries.get(stem).focus if stem in entries else ""
            )
            title = titles.get(file.name) or focus
            if not title and role != "router":
                title = _session_summary(file)
            title = title or stem
            rows.append(
                {
                    "path": path,
                    "title": title,
                    "focus": focus,
                    "role": role,
                    "status": handle.status if handle is not None else "idle",
                    "last_report": handle.last_report if handle is not None else "",
                }
            )
        for path, handle in self._handles.items():
            if path in seen:
                continue
            file = Path(path)
            title = (
                titles.get(file.name)
                or handle.focus
                or _session_summary(file)
                or handle.title
                or file.stem
            )
            rows.append(
                {
                    "path": path,
                    "title": title,
                    "focus": handle.focus,
                    "role": "worker",
                    "status": handle.status,
                    "last_report": handle.last_report,
                }
            )
        rows.sort(key=lambda row: (0 if row["role"] == "router" else 1, row["title"]))
        return rows

    # -- mutations -----------------------------------------------------------

    def focus(self, session_path: str, focus: str) -> None:
        """Set what one worker session concentrates on."""
        handle = self._handles.get(str(session_path))
        if handle is not None:
            handle.focus = focus
        roster_set_focus(self.sessions_dir, Path(session_path).stem, focus)
        self._notify()

    def spawn(self, focus: str) -> str:
        """Create a new lazy worker session, tracked under ``focus``."""
        path = unique_session_path(root=self.root)
        open_session(path, lazy=True, cwd=self.root)
        roster_set_focus(self.sessions_dir, path.stem, focus)
        self._handles[str(path)] = WorkerHandle(str(path), path.stem, focus)
        self._notify()
        return str(path)

    def _notify(self) -> None:
        if self.on_change is not None:
            # A UI hiccup must not break dispatch.
            with contextlib.suppress(Exception):
                self.on_change()

    def _resolve(self, session: str) -> Path:
        candidate = Path(session).expanduser()
        if not candidate.suffix:
            candidate = self.sessions_dir / f"{candidate.name}.jsonl"
        return candidate

    async def dispatch(self, session: str, task: str) -> str:
        """Assign ``task`` to one worker session; returns its new status.

        The router session cannot be dispatched (it *is* the coordinator); a
        worker that is working or queued refuses further work; when the
        concurrency cap is reached the dispatch queues.
        """
        path = self._resolve(session)
        if str(path) == self.router_path:
            raise ValueError("cannot dispatch the router session")
        handle = self._handles.get(str(path))
        if handle is not None and handle.status in ("working", "queued"):
            raise ValueError(f"worker is {handle.status}: {path.name}")
        if self._closing:
            raise ValueError("fleet is closing")
        working = sum(1 for h in self._handles.values() if h.status == "working")
        queued = handle is not None and handle.status == "queued"
        if handle is None:
            stem = path.stem
            entry = read_roster(self.sessions_dir).get(stem)
            handle = WorkerHandle(str(path), stem, entry.focus if entry else "")
            self._handles[str(path)] = handle
        # A session the router picks for work becomes a tracked worker even
        # when it predates the roster; otherwise it would keep showing up as a
        # coding session after the first dispatch.
        roster_set_focus(self.sessions_dir, path.stem, handle.focus)
        handle.status = "queued" if (working >= self.max_concurrent or queued) else "working"
        handle.stopped = False
        handle.task = asyncio.create_task(self._run(handle, task))
        self._notify()
        return handle.status

    async def _run(self, handle: WorkerHandle, task: str) -> None:
        async with self._sem:
            if self._closing:
                handle.status = "blocked"
                return
            handle.status = "working"
            self._notify()
            try:
                prepared = await self._build_worker(handle)
                # prepare returns the PreparedFrontend envelope; the worker is
                # the Runtime inside it (agent/close live there).
                runtime = prepared.runtime
                handle.agent = runtime.agent
                handle.runtime = runtime
                # The build can take tens of seconds (MCP handshake); close()
                # may have landed meanwhile. Tear down instead of starting a run
                # the user already exited — the finally closes handle.runtime.
                if self._closing:
                    handle.status = "blocked"
                    return
                if handle.stopped:
                    handle.agent.abort()
                    handle.status = "blocked"
                    return
                if self.run_timeout_s > 0:
                    try:
                        await asyncio.wait_for(
                            runtime.agent.prompt(worker_prompt(handle.focus, task)),
                            self.run_timeout_s,
                        )
                    except TimeoutError:
                        handle.agent.abort()
                        handle.status = "failed"
                        handle.last_report = "运行超时"
                        self._inject(handle, handle.last_report)
                        return
                else:
                    await runtime.agent.prompt(worker_prompt(handle.focus, task))
                if self._closing:
                    return
                report = self._last_report(handle.agent)
                if handle.stopped:
                    handle.status = "blocked"
                    handle.last_report = report or "已停止"
                    self._inject(handle, handle.last_report or "已停止")
                else:
                    handle.status = "waiting"
                    handle.last_report = report
                    self._inject(handle, report or "（worker 结束但未给出文本报告）")
            except Exception as exc:  # noqa: BLE001 - worker failures are reports
                if self._closing:
                    return
                handle.status = "failed"
                handle.last_report = f"{type(exc).__name__}: {exc}"[: self.report_limit]
                self._inject(handle, handle.last_report)
            finally:
                if self.supervisor is not None:
                    self.supervisor.unregister(_supervisor_key(handle))
                if handle.runtime is not None:
                    # Closing must not raise.
                    with contextlib.suppress(Exception):
                        await handle.runtime.close()
                self._notify()

    async def _build_worker(self, handle: WorkerHandle) -> Any:
        # The default keeps the router's mode but never inherits the auto
        # bypass: auto approves mutations, so workers are downgraded to ask to
        # surface approvals. ``auto`` opts into the bypass explicitly.
        if self.worker_mode == "inherit":
            mode = "ask" if self.mode == "auto" else self.mode
        else:
            mode = self.worker_mode
        path = Path(handle.path)
        if not path.is_file():
            # ``spawn`` registers a lazy session whose file appears only on
            # the first message. Resuming that missing path would make the
            # worker runtime open a brand-new session with a -N suffix, so the
            # registered worker identity and its transcript would diverge.
            with contextlib.suppress(OSError):
                open_session(path, lazy=True, cwd=self.root).materialize()
        profile = AgentProfile(
            id=handle.title or Path(handle.path).stem,
            kind="zen_worker",
            label=handle.focus,
        )
        prepared = await prepare(
            RuntimeOptions(root=self.root, resume=handle.path, mode=mode, profile=profile),
            views=False,
        )
        router_runtime = getattr(self.chat, "runtime", None)
        update_run_scope(
            prepared.runtime,
            kind="zen_worker",
            agentId=handle.title or Path(handle.path).stem,
            label=handle.focus,
            parentRunId=getattr(router_runtime, "current_run_id", None),
            workItemId=None,
            taskId=None,
        )
        if self.confirm is not None:
            # Worker write/exec calls ask the router's UI for approval.
            prepared.broker.set_confirm(self.confirm)
        if self.supervisor is not None:
            self.supervisor.register(
                _supervisor_key(handle),
                profile=profile,
                runtime=prepared.runtime,
                session=prepared.runtime.session,
                parentRunId=getattr(router_runtime, "current_run_id", None),
            )
        return prepared

    def _last_report(self, agent: Any) -> str:
        for message in reversed(getattr(agent, "messages", [])):
            if not isinstance(message, AssistantMessage):
                continue
            chunks = [
                block.text
                for block in getattr(message, "content", []) or []
                if getattr(block, "text", "")
            ]
            if chunks:
                return "\n".join(chunks)[: self.report_limit]
        return ""

    def _inject(self, handle: WorkerHandle, text: str) -> None:
        """Buffer one worker report and schedule the batch flush.

        A wake task so a finished worker run never blocks the semaphore lane:
        ``ChatController.deliver`` steers into a streaming turn, wakes an idle
        router with a run of its own, and queues under compaction — the dsh
        rule that results arrive on their own. Reports arriving inside the
        batch window (or during an in-flight delivery) ride the same message.
        """
        if self.chat is None:
            return
        self._report_buffer.append(f"[zen:worker {handle.title}] {text}")
        if self._wake_task is None or self._wake_task.done():
            self._wake_task = asyncio.get_running_loop().create_task(
                self._flush_reports()
            )

    def _router_visible(self) -> bool:
        """Whether the slot's visible session is this fleet's router.

        Reports wake the router through the shared ChatController, which
        follows whatever session the slot has active. Steering them into any
        other session would let a coding chat act as the router; a chat that
        exposes no runtime (a test double) keeps the old behaviour.
        """
        runtime = getattr(self.chat, "runtime", None)
        path = getattr(getattr(runtime, "session", None), "path", None)
        if path is None:
            return True
        try:
            return Path(path).resolve() == Path(self.router_path).resolve()
        except OSError:
            return False

    async def _flush_reports(self) -> None:
        """Deliver buffered reports as one merged message, in a loop so a
        report arriving mid-delivery is never delivered one-per-run."""
        try:
            while self._report_buffer and not self._closing:
                if self.batch_window_s:
                    await asyncio.sleep(self.batch_window_s)
                if not self._router_visible():
                    # The visible session is not the router (a coding session
                    # was opened while the fleet lives). Never steer another
                    # chat; the handle keeps the report for the roster.
                    _log.warning(
                        "dropping %d zen report(s): router %s is not the active session",
                        len(self._report_buffer),
                        self.router_path,
                    )
                    self._report_buffer.clear()
                    return
                batch, self._report_buffer = self._report_buffer[:], []
                joined = "\n\n".join(batch)
                deliver = getattr(self.chat, "deliver", None)
                if deliver is not None:
                    await self._safe_deliver(deliver, joined)
                else:
                    # A dead router must not kill workers.
                    with contextlib.suppress(Exception):
                        self.chat.steer(joined)
        finally:
            # A flush cancelled during teardown must not spawn an orphan task on
            # a closing loop. cancelling() (not cancelled()) reports the pending
            # cancel while this finally runs.
            if (
                self._report_buffer
                and not self._closing
                and not asyncio.current_task().cancelling()
            ):
                self._wake_task = asyncio.get_running_loop().create_task(
                    self._flush_reports()
                )

    async def _safe_deliver(self, deliver, message: str) -> None:
        with contextlib.suppress(Exception):
            await deliver(message)

    async def stop(self, session_path: str) -> bool:
        """Abort one worker's run; True when a live run was stopped."""
        handle = self._handles.get(str(session_path))
        if handle is None or handle.status not in ("working", "queued"):
            return False
        handle.stopped = True
        if handle.task is not None and not handle.task.done():
            if handle.status == "queued":
                handle.task.cancel()
                handle.status = "blocked"
                self._notify()
                return True
            if handle.agent is not None:
                handle.agent.abort()
            handle.status = "blocked"
        self._notify()
        return True

    async def wait_all(self) -> None:
        """Test/drain helper: wait until dispatch and report-flush settle.

        Loops because a flush cycle may schedule its successor (or workers
        may schedule the first flush) while we wait.
        """
        while True:
            tasks = [
                h.task
                for h in self._handles.values()
                if h.task is not None and not h.task.done()
            ]
            if self._wake_task is not None and not self._wake_task.done():
                tasks.append(self._wake_task)
            if not tasks:
                return
            await asyncio.gather(*tasks, return_exceptions=True)

    async def close(self) -> None:
        """Abort every live worker; the fleet is done for this slot."""
        self._closing = True
        for handle in self._handles.values():
            if handle.agent is not None:
                # Teardown must not raise.
                with contextlib.suppress(Exception):
                    handle.agent.abort()
            if handle.status in ("working", "queued"):
                handle.status = "blocked"
        self._notify()
