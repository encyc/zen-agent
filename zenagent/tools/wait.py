"""The wait tool: collect the result of a background bash job.

Background jobs are started by ``bash`` with ``async: true``. ``wait`` is a
read-tier tool — it never changes the machine — and it is declared
``execution_mode="sequential"`` so it runs *after* the parallel tool batch that
may contain the ``bash`` call which starts the job it is waiting for.
"""

from __future__ import annotations

import asyncio
import contextlib

from zenagent.agent.loop import AgentTool, ToolResultAgent
from zenagent.prompts import tool_description
from zenagent.tools._result import text_result
from zenagent.tools.jobs import (
    Job,
    UnknownJobError,
    get_job_manager,
)
from zenagent.tools.task import UnknownTaskError, get_task_manager

__all__ = ["DEFAULT_WAIT_TIMEOUT_S", "MAX_WAIT_TIMEOUT_S", "WAIT"]

#: Default wait budget when the model does not say.
DEFAULT_WAIT_TIMEOUT_S = 120.0
#: Bounds ``timeout_s`` is clamped to.
MIN_WAIT_TIMEOUT_S = 1.0
MAX_WAIT_TIMEOUT_S = 600.0


def _details(job: Job) -> dict:
    return {
        "job_id": job.id,
        "status": job.status,
        "exit_code": job.exit_code,
        "timed_out": job.timed_out,
        "duration_ms": job.duration_ms,
        "truncated": job.truncated,
    }


def _render_final(job: Job) -> str:
    duration = (
        f", {job.duration_ms / 1000:.2f}s" if job.duration_ms is not None else ""
    )
    if job.status == "timed_out":
        header = (
            f"job {job.id} timed out after {job.timeout_s:g}s "
            f"(exit code: {job.exit_code})"
        )
    else:
        header = (
            f"job {job.id}: {job.status} "
            f"(exit code: {job.exit_code}{duration})"
        )
    body = job.output
    if body:
        return f"{header}\n{body}"
    return header


def _render_running(job: Job, waited_s: float) -> str:
    lines = [
        f"job {job.id} still running after {waited_s:g}s: {job.command}",
        (
            f"call wait again with job_id={job.id!r} for the result "
            f"(background deadline: {job.timeout_s:g}s)"
        ),
    ]
    body = job.output
    if body:
        lines.append(body)
    return "\n".join(lines)


async def _wait_for_result(
    manager, job_id: str | None, timeout_s: float, abort
) -> tuple[Job | None, bool]:
    """Run ``manager.wait`` raced against the user abort token.

    Returns ``(job, aborted)``. An abort does not kill the background jobs —
    they keep running and a later wait can still collect them.
    """
    wait_task = asyncio.create_task(manager.wait(job_id, timeout_s=timeout_s))
    if abort is None:
        return await wait_task, False
    abort_task = asyncio.create_task(abort.wait())
    aborted = False
    try:
        done, _ = await asyncio.wait(
            {wait_task, abort_task}, return_when=asyncio.FIRST_COMPLETED
        )
        aborted = abort_task in done and wait_task not in done
    finally:
        abort_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await abort_task
        if not wait_task.done():
            wait_task.cancel()
    if aborted:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await wait_task
        return None, True
    return await wait_task, False


async def _wait_task(task_id: str, timeout_s: float, abort) -> ToolResultAgent:
    """Collect a background ``task`` child (``task_N`` ids from async dispatch).

    Children keep running across waits, aborts and session switches: a wait
    only observes — the same contract as bash jobs. A completed run answers
    immediately and stays waitable until retention prunes it.
    """
    manager = get_task_manager()
    wait_task = asyncio.create_task(manager.wait(task_id, timeout_s=timeout_s))
    if abort is None:
        run = await wait_task
    else:
        abort_watch = asyncio.create_task(abort.wait())
        aborted = False
        try:
            done, _ = await asyncio.wait(
                {wait_task, abort_watch}, return_when=asyncio.FIRST_COMPLETED
            )
            aborted = abort_watch in done and wait_task not in done
        finally:
            abort_watch.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await abort_watch
            if not wait_task.done():
                wait_task.cancel()
        if aborted:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await wait_task
            known = ", ".join(manager.ids()) or "none"
            return text_result(
                "aborted while waiting: background tasks keep running; call wait "
                f"again to collect the result (known tasks: {known})",
                aborted=True,
                task_ids=manager.ids(),
            )
        run = await wait_task
    if run.status == "running":
        return text_result(
            f"task {run.task_id} still running after {timeout_s:g}s: {run.name}\n"
            f"call wait again with job_id={run.task_id!r} for the result",
            task_id=run.task_id,
            name=run.name,
            status="running",
        )
    # The full envelope text plus the run's details: subagent_cost/tokens ride
    # along on the FIRST collection so spend accounting and telemetry see
    # background children; a re-wait re-reads the envelope text but must not
    # double-count the run into the HUD or the parent's max_cost guard.
    details = dict(run.details)
    if run.spend_reported:
        details.pop("subagent_cost", None)
        details.pop("subagent_tokens", None)
    else:
        run.spend_reported = True
    return text_result(run.envelope, task_id=run.task_id, **details)


def _task_unknown(exc: UnknownTaskError) -> ToolResultAgent:
    known = ", ".join(exc.known) if exc.known else "none"
    return text_result(
        f"unknown task id {exc.task_id!r}; known tasks: {known}",
        is_error=True,
        task_id=exc.task_id,
        known_task_ids=list(exc.known),
    )


async def _wait(
    tool_call_id: str, args: dict, abort, on_update=None
) -> ToolResultAgent:
    raw_timeout = args.get("timeout_s")
    try:
        timeout_s = (
            DEFAULT_WAIT_TIMEOUT_S if raw_timeout is None else float(raw_timeout)
        )
    except (TypeError, ValueError):
        return text_result(
            f"timeout_s must be a number (got {raw_timeout!r})", is_error=True
        )
    timeout_s = min(max(timeout_s, MIN_WAIT_TIMEOUT_S), MAX_WAIT_TIMEOUT_S)

    job_id = args.get("job_id")
    if job_id is not None:
        job_id = str(job_id).strip() or None

    if job_id is not None and job_id.startswith("task_"):
        try:
            return await _wait_task(job_id, timeout_s, abort)
        except UnknownTaskError as exc:
            return _task_unknown(exc)

    manager = get_job_manager()
    try:
        job, aborted = await _wait_for_result(
            manager, job_id, timeout_s, abort
        )
    except UnknownJobError as exc:
        known = ", ".join(exc.known) if exc.known else "none"
        return text_result(
            f"unknown job id {exc.job_id!r}; known jobs: {known}",
            is_error=True,
            job_id=exc.job_id,
            known_job_ids=list(exc.known),
        )
    if aborted:
        known = ", ".join(manager.ids()) or "none"
        return text_result(
            "aborted while waiting: background jobs keep running; call wait "
            f"again to collect the result (known jobs: {known})",
            aborted=True,
            job_ids=manager.ids(),
        )
    if job is None:
        return text_result(
            "nothing to wait for: no background jobs are running or finished"
        )
    if job.running:
        return text_result(_render_running(job, timeout_s), **_details(job))
    return text_result(_render_final(job), **_details(job))


WAIT = AgentTool(
    name="wait",
    description=tool_description("wait"),
    parameters={
        "type": "object",
        "properties": {
            "job_id": {
                "type": "string",
                "description": (
                    "Job id reported by bash async: true "
                    "(default: any running job)."
                ),
            },
            "timeout_s": {
                "type": "number",
                "default": DEFAULT_WAIT_TIMEOUT_S,
                "description": (
                    "Maximum seconds to wait (default 120, clamped to 1-600)."
                ),
            },
        },
        "required": [],
    },
    execute=_wait,
    approval="read",
    execution_mode="sequential",
)
