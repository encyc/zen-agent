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
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from zenagent.agent.session import read_session_titles, unique_session_path
from zenagent.app.bootstrap import prepare
from zenagent.app.records import open_session
from zenagent.app.runtime import RuntimeOptions
from zenagent.app.zen.prompts import worker_prompt
from zenagent.app.zen.roster import read_roster
from zenagent.app.zen.roster import set_focus as roster_set_focus

if TYPE_CHECKING:
    from zenagent.app.chat import ChatController

__all__ = ["REPORT_LIMIT", "RosterRow", "WorkerHandle", "ZenFleet", "worker_statuses"]

#: Worker states the roster reports. ``router`` rows carry ``idle`` status and
#: the ``router`` role instead.
worker_statuses = ("idle", "queued", "working", "waiting", "blocked", "failed")

#: A worker report is capped so the router conversation stays readable.
REPORT_LIMIT = 800

RosterRow = dict[str, str]


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
    ) -> None:
        self.chat = chat
        self.root = Path(root)
        self.sessions_dir = Path(sessions_dir)
        self.router_path = str(router_path)
        self.max_concurrent = max(1, int(max_concurrent))
        self.on_change = on_change
        self._handles: dict[str, WorkerHandle] = {}
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
        rows: list[RosterRow] = []
        seen: set[str] = set()
        for file in sorted(self.sessions_dir.glob("*.jsonl")):
            stem = file.stem
            path = str(file)
            seen.add(path)
            role = "router" if path == self.router_path else entries.get(stem).role if stem in entries else "worker"
            handle = self._handles.get(path)
            focus = (
                handle.focus
                if handle is not None and handle.focus
                else entries.get(stem).focus if stem in entries else ""
            )
            rows.append(
                {
                    "path": path,
                    "title": titles.get(file.name) or stem,
                    "focus": focus,
                    "role": role,
                    "status": handle.status if handle is not None else "idle",
                    "last_report": handle.last_report if handle is not None else "",
                }
            )
        for path, handle in self._handles.items():
            if path not in seen:
                rows.append(
                    {
                        "path": path,
                        "title": handle.title or Path(path).stem,
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
                runtime = await self._build_worker(handle)
                handle.agent = runtime.agent
                handle.runtime = runtime
                if handle.stopped:
                    handle.agent.abort()
                    handle.status = "blocked"
                    return
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
                handle.last_report = f"{type(exc).__name__}: {exc}"[:REPORT_LIMIT]
                self._inject(handle, handle.last_report)
            finally:
                if handle.runtime is not None:
                    # Closing must not raise.
                    with contextlib.suppress(Exception):
                        await handle.runtime.close()
                self._notify()

    async def _build_worker(self, handle: WorkerHandle) -> Any:
        return await prepare(
            RuntimeOptions(root=self.root, resume=handle.path),
            views=False,
        )

    @staticmethod
    def _last_report(agent: Any) -> str:
        for message in reversed(getattr(agent, "messages", [])):
            if getattr(message, "role", None) != "assistant":
                continue
            chunks = [
                block.text
                for block in getattr(message, "content", []) or []
                if getattr(block, "text", "")
            ]
            if chunks:
                return "\n".join(chunks)[:REPORT_LIMIT]
        return ""

    def _inject(self, handle: WorkerHandle, text: str) -> None:
        if self.chat is None:
            return
        # A dead router must not kill workers.
        with contextlib.suppress(Exception):
            self.chat.steer(f"[zen:worker {handle.title}] {text}")

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
        """Test/drain helper: wait for every dispatch task to settle."""
        tasks = [h.task for h in self._handles.values() if h.task is not None]
        if tasks:
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
