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
from zenagent.tools.jobs import (
    Job,
    UnknownJobError,
    get_job_manager,
)
from zenagent.types import TextContent

__all__ = ["DEFAULT_WAIT_TIMEOUT_S", "MAX_WAIT_TIMEOUT_S", "WAIT"]

#: Default wait budget when the model does not say.
DEFAULT_WAIT_TIMEOUT_S = 120.0
#: Bounds ``timeout_s`` is clamped to.
MIN_WAIT_TIMEOUT_S = 1.0
MAX_WAIT_TIMEOUT_S = 600.0


def _text(text: str, *, is_error: bool = False, **details) -> ToolResultAgent:
    return ToolResultAgent(
        content=[TextContent(text=text)], details=details, is_error=is_error
    )


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


async def _wait(
    tool_call_id: str, args: dict, abort, on_update=None
) -> ToolResultAgent:
    raw_timeout = args.get("timeout_s")
    try:
        timeout_s = (
            DEFAULT_WAIT_TIMEOUT_S if raw_timeout is None else float(raw_timeout)
        )
    except (TypeError, ValueError):
        return _text(
            f"timeout_s must be a number (got {raw_timeout!r})", is_error=True
        )
    timeout_s = min(max(timeout_s, MIN_WAIT_TIMEOUT_S), MAX_WAIT_TIMEOUT_S)

    job_id = args.get("job_id")
    if job_id is not None:
        job_id = str(job_id).strip() or None

    manager = get_job_manager()
    try:
        job, aborted = await _wait_for_result(
            manager, job_id, timeout_s, abort
        )
    except UnknownJobError as exc:
        known = ", ".join(exc.known) if exc.known else "none"
        return _text(
            f"unknown job id {exc.job_id!r}; known jobs: {known}",
            is_error=True,
            job_id=exc.job_id,
            known_job_ids=list(exc.known),
        )
    if aborted:
        known = ", ".join(manager.ids()) or "none"
        return _text(
            "aborted while waiting: background jobs keep running; call wait "
            f"again to collect the result (known jobs: {known})",
            aborted=True,
            job_ids=manager.ids(),
        )
    if job is None:
        return _text(
            "nothing to wait for: no background jobs are running or finished"
        )
    if job.running:
        return _text(_render_running(job, timeout_s), **_details(job))
    return _text(_render_final(job), **_details(job))


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
