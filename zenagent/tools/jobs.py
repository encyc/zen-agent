"""Background jobs shared by the async bash tool and the wait tool.

A :class:`Job` is one command started through
:func:`zenagent.tools.shell.spawn_shell_process` in its own process group; a
:class:`JobManager` owns the live jobs of one event loop.

Design notes:

* no polling: every job has a completion ``asyncio.Event`` set exactly once by
  its monitor task, and :meth:`JobManager.wait` waits on that event with a
  timeout (plus the job's own deadline race);
* one monitor task per job owns the pipe, the deadline and the final status, so
  a timeout or a cancellation can never leave an orphan process or a reader
  task behind;
* status lifecycle: ``running`` → ``ok`` (exit 0) | ``failed`` (non-zero exit
  or spawn/pump error) | ``timed_out`` (the deadline killed the group);
* ``cleanup-on-wait``: once :meth:`JobManager.wait` hands back a *finished*
  job, it is removed from the manager. A still-running snapshot (wait timeout)
  keeps the job so the model can wait again.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from zenagent.tools.shell import (
    DRAIN_TIMEOUT_S,
    READ_CHUNK,
    OutputBuffer,
    ShellError,
    command_env,
    kill_process_group,
    resolve_cwd,
    spawn_shell_process,
)

__all__ = [
    "DEFAULT_JOB_TIMEOUT_S",
    "MAX_JOB_TIMEOUT_S",
    "MIN_JOB_TIMEOUT_S",
    "Job",
    "JobManager",
    "JobStartError",
    "JobStatus",
    "UnknownJobError",
    "close_job_manager",
    "get_job_manager",
]

JobStatus = Literal["running", "ok", "failed", "timed_out"]

#: Default deadline for a background bash job.
DEFAULT_JOB_TIMEOUT_S = 600.0
#: Bounds the bash tool clamps a model-supplied background deadline to.
MIN_JOB_TIMEOUT_S = 1.0
MAX_JOB_TIMEOUT_S = 3600.0


class JobStartError(RuntimeError):
    """A background job could not be started (bad cwd, spawn failure, ...)."""


class UnknownJobError(KeyError):
    """A wait named a job id the manager does not know."""

    def __init__(self, job_id: str, known: list[str]) -> None:
        super().__init__(job_id)
        self.job_id = job_id
        #: Ids still known to the manager, in start order.
        self.known = known

    def __str__(self) -> str:
        return f"unknown job id {self.job_id!r}"


@dataclass
class Job:
    """One background command and the state the wait tool reports."""

    id: str
    command: str
    status: JobStatus = "running"
    exit_code: int | None = None
    timeout_s: float = DEFAULT_JOB_TIMEOUT_S
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    timed_out: bool = False
    duration_ms: int | None = None
    error: str = ""
    _buffer: OutputBuffer = field(default_factory=OutputBuffer, repr=False)
    _proc: asyncio.subprocess.Process | None = field(default=None, repr=False)
    _task: asyncio.Task | None = field(default=None, repr=False)
    _done: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    @property
    def output(self) -> str:
        """Bounded head+tail output captured so far (also while running)."""
        return self._buffer.text()

    @property
    def truncated(self) -> bool:
        return self._buffer.truncated

    @property
    def running(self) -> bool:
        return self.status == "running"


class JobManager:
    """Own the background jobs of one event loop."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._counter = itertools.count(1)
        self._loop: asyncio.AbstractEventLoop | None = None

    # -- introspection ----------------------------------------------------

    def snapshot(self, job_id: str) -> Job | None:
        """The job with ``job_id`` (finished jobs survive until waited)."""
        return self._jobs.get(job_id)

    def list(self) -> list[Job]:
        """Every known job, in start order."""
        return list(self._jobs.values())

    def ids(self) -> list[str]:
        """Known job ids, in start order."""
        return [job.id for job in self._jobs.values()]

    # -- lifecycle --------------------------------------------------------

    async def start(
        self,
        command: str,
        *,
        cwd: str | Path | None = None,
        timeout_s: float = DEFAULT_JOB_TIMEOUT_S,
    ) -> Job:
        """Start ``command`` in the background and return its ``Job``.

        The process is spawned (not merely scheduled) before returning, so a
        bad cwd or an unknown shell fails this call instead of a later wait.
        Raises :class:`ShellError`, :class:`JobStartError`.
        """
        self._loop = asyncio.get_running_loop()
        target = resolve_cwd(cwd)
        if isinstance(target, str):
            raise JobStartError(target)
        job = Job(
            id=f"bg_{next(self._counter)}",
            command=command,
            timeout_s=timeout_s,
        )
        try:
            proc = await spawn_shell_process(
                command, cwd=target, env=command_env()
            )
        except ShellError:
            raise
        except OSError as exc:
            raise JobStartError(f"cannot start command: {exc}") from exc
        job._proc = proc
        self._jobs[job.id] = job
        job._task = asyncio.create_task(self._monitor(job, proc, timeout_s))
        return job

    async def wait(
        self, job_id: str | None = None, timeout_s: float = 120.0
    ) -> Job | None:
        """Wait for ``job_id`` (or any job) and return its snapshot.

        Returns ``None`` when no job is known at all. A finished job is
        returned immediately and removed from the manager (cleanup-on-wait); a
        wait that times out returns the still-running job without killing it.
        Raises :class:`UnknownJobError` for an unknown explicit ``job_id``.
        """
        self._loop = asyncio.get_running_loop()
        job = self._select(job_id)
        if job is None:
            return None
        if not job.running:
            self._retire(job)
            return job
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(
                job._done.wait(), timeout=max(float(timeout_s), 0.0)
            )
        if not job.running:
            self._retire(job)
        return job

    async def close(self) -> None:
        """Cancel every job, kill its process group and drop it.

        Used on shutdown and by tests; idempotent. Jobs are killed first so a
        cancelled monitor cannot be stuck waiting on a live child.
        """
        jobs = list(self._jobs.values())
        self._jobs.clear()
        for job in jobs:
            if job._task is not None and not job._task.done():
                job._task.cancel()
        for job in jobs:
            if job._task is not None:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await job._task
            if job._proc is not None and job._proc.returncode is None:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await kill_process_group(job._proc)

    # -- internals --------------------------------------------------------

    def _select(self, job_id: str | None) -> Job | None:
        """Pick the job to wait for; None when the manager is empty."""
        if job_id is not None:
            job = self._jobs.get(str(job_id))
            if job is None:
                raise UnknownJobError(str(job_id), self.ids())
            return job
        running = [job for job in self._jobs.values() if job.running]
        if running:
            return min(running, key=lambda job: (job.started_at, job.id))
        finished = [job for job in self._jobs.values() if not job.running]
        if finished:
            return max(
                finished, key=lambda job: (job.finished_at or 0.0, job.id)
            )
        return None

    def _retire(self, job: Job) -> None:
        """Drop a finished job (cleanup-on-wait)."""
        self._jobs.pop(job.id, None)

    def _abandon(self) -> None:
        """Drop every job without awaiting: used when the loop changed."""
        jobs = list(self._jobs.values())
        self._jobs.clear()
        for job in jobs:
            if job._task is not None and not job._task.done():
                job._task.cancel()
            if job._proc is not None and job._proc.returncode is None:
                with contextlib.suppress(OSError, ProcessLookupError):
                    job._proc.kill()

    async def _pump(self, job: Job, proc: asyncio.subprocess.Process) -> None:
        """Read the merged output pipe until EOF; never raises."""
        assert proc.stdout is not None
        while True:
            try:
                chunk = await proc.stdout.read(READ_CHUNK)
            except OSError:
                return
            if not chunk:
                return
            job._buffer.add(chunk)

    async def _monitor(
        self,
        job: Job,
        proc: asyncio.subprocess.Process,
        timeout_s: float,
    ) -> None:
        """Own one job from spawn to final status (the only waiter on it)."""
        read_task = asyncio.create_task(self._pump(job, proc))
        started = time.monotonic()
        try:
            await self._watch(job, proc, read_task, timeout_s, started)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a job must never die silently
            job.error = f"{type(exc).__name__}: {exc}"
        finally:
            await self._finish(job, proc, read_task, started)

    async def _watch(
        self,
        job: Job,
        proc: asyncio.subprocess.Process,
        read_task: asyncio.Task,
        timeout_s: float,
        started: float,
    ) -> None:
        """Race the output reader against the job's deadline."""
        done, _ = await asyncio.wait({read_task}, timeout=timeout_s)
        if not done:
            # The deadline fired. If the process happened to exit in the same
            # instant this is a normal completion, not a timeout.
            if proc.returncode is None:
                job.timed_out = True
                await kill_process_group(proc)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(read_task, timeout=DRAIN_TIMEOUT_S)
            return
        # stdout hit EOF: the process normally exited. Give it the remainder
        # of its budget to be reaped, then take the group down.
        if proc.returncode is None:
            remaining = max(
                timeout_s - (time.monotonic() - started), 0.0
            )
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(proc.wait(), timeout=remaining)
        if proc.returncode is None:
            job.timed_out = True
            await kill_process_group(proc)

    async def _finish(
        self,
        job: Job,
        proc: asyncio.subprocess.Process,
        read_task: asyncio.Task,
        started: float,
    ) -> None:
        """Cancel the reader, make sure the process is dead, publish status."""
        try:
            if not read_task.done():
                read_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await read_task
        finally:
            try:
                if proc.returncode is None:
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await kill_process_group(proc)
            finally:
                job.exit_code = proc.returncode
                if job.running:
                    if job.timed_out:
                        job.status = "timed_out"
                    elif job.error or job.exit_code != 0:
                        job.status = "failed"
                    else:
                        job.status = "ok"
                job.finished_at = time.time()
                job.duration_ms = round(
                    (time.monotonic() - started) * 1000
                )
                job._done.set()


_manager: JobManager | None = None


def get_job_manager() -> JobManager:
    """Process-wide job manager bound to the current event loop.

    A new manager is created when there is none or when the running loop
    changed: jobs belong to the loop that spawned them, and a closed test/app
    loop must not leak tasks into the next one. The old manager's jobs are
    abandoned (tasks cancelled, processes killed best-effort), never awaited.
    """
    global _manager
    loop = asyncio.get_running_loop()
    if _manager is None or _manager._loop is not loop:
        if _manager is not None:
            _manager._abandon()
        _manager = JobManager()
        _manager._loop = loop
    return _manager


async def close_job_manager() -> None:
    """Close and forget the process-wide manager (app shutdown)."""
    global _manager
    manager, _manager = _manager, None
    if manager is not None:
        await manager.close()
