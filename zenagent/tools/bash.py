"""The bash tool: run a shell command, stream its output, never hang the agent.

Behaviour decisions (all from the plan):

* stdout and stderr are merged — the model wants one narrative, not two.
* output larger than :data:`MAX_OUTPUT_BYTES` keeps the head *and* the tail
  with a marker in between; the middle is what you can afford to lose.
* a non-zero exit code is **not** an error result: command failure is normal
  information, so it is reported in the content (``is_error`` stays False).
* a timeout or a user abort kills the whole process *group* (child processes
  included) and returns whatever output had arrived.
* while the command runs, partial output is pushed through ``on_update``
  (throttled by size and time) so the UI can show progress.
* ``async: true`` starts the command as a background job (:mod:`zenagent.tools.
  jobs`) and returns immediately with its id; the wait tool collects the
  result. Background jobs use the same shell, environment, cwd and output cap.

The shell itself is resolved by :mod:`zenagent.tools.shell`, so foreground and
background commands spawn through exactly the same ``(executable, argv)`` path.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from pathlib import Path

from zenagent.agent.loop import AgentTool, ToolResultAgent
from zenagent.events import AbortToken
from zenagent.tools.jobs import (
    DEFAULT_JOB_TIMEOUT_S,
    MAX_JOB_TIMEOUT_S,
    MIN_JOB_TIMEOUT_S,
    JobStartError,
    get_job_manager,
)
from zenagent.tools.shell import (
    DRAIN_TIMEOUT_S,
    MAX_OUTPUT_BYTES,
    NON_INTERACTIVE_ENV,
    READ_CHUNK,
    OutputBuffer,
    ShellError,
    command_env,
    decode_output,
    kill_process_group,
    resolve_cwd,
    spawn_shell_process,
)
from zenagent.types import TextContent

__all__ = ["BASH", "MAX_OUTPUT_BYTES", "NON_INTERACTIVE_ENV"]

#: Minimum accumulated bytes between two progress updates.
_UPDATE_BYTES = 4096
#: Minimum seconds between two progress updates.
_UPDATE_INTERVAL_S = 0.5


def _text(text: str, *, is_error: bool = False, **details) -> ToolResultAgent:
    return ToolResultAgent(
        content=[TextContent(text=text)], details=details, is_error=is_error
    )


async def _background(
    command: str, args: dict, cwd: Path
) -> ToolResultAgent:
    """Start ``command`` as a background job and report its id immediately."""
    raw_timeout = args.get("timeout_s")
    try:
        timeout_s = (
            DEFAULT_JOB_TIMEOUT_S if raw_timeout is None else float(raw_timeout)
        )
    except (TypeError, ValueError):
        return _text(
            f"timeout_s must be a number (got {raw_timeout!r})", is_error=True
        )
    timeout_s = min(max(timeout_s, MIN_JOB_TIMEOUT_S), MAX_JOB_TIMEOUT_S)
    try:
        job = await get_job_manager().start(
            command, cwd=cwd, timeout_s=timeout_s
        )
    except (ShellError, JobStartError) as exc:
        return _text(str(exc), is_error=True)
    return _text(
        f"started background job {job.id} (running): {command}",
        job_id=job.id,
        status="running",
        **{"async": True},
    )


async def _bash(
    tool_call_id: str, args: dict, abort: AbortToken | None, on_update=None
) -> ToolResultAgent:
    command = args["command"]
    is_async = args.get("async", False)
    if is_async is None:
        is_async = False
    if not isinstance(is_async, bool):
        return _text("async must be a boolean", is_error=True)
    cwd = resolve_cwd(args.get("cwd"))
    if isinstance(cwd, str):
        return _text(cwd, is_error=True)
    if is_async:
        return await _background(command, args, cwd)
    timeout_s = float(args.get("timeout_s") or 120.0)
    started = time.monotonic()

    try:
        proc = await spawn_shell_process(command, cwd=cwd, env=command_env())
    except ShellError as exc:
        return _text(str(exc), is_error=True)
    except OSError as exc:
        return _text(f"cannot start command: {exc}", is_error=True)

    assert proc.stdout is not None
    buffer = OutputBuffer()
    last_update_at = 0.0
    last_update_bytes = 0

    def emit_progress(force: bool = False) -> None:
        nonlocal last_update_at, last_update_bytes
        if on_update is None:
            return
        now = time.monotonic()
        grew = buffer.total - last_update_bytes
        if not force and grew < _UPDATE_BYTES and now - last_update_at < _UPDATE_INTERVAL_S:
            return
        last_update_at, last_update_bytes = now, buffer.total
        on_update(
            ToolResultAgent(
                content=[
                    TextContent(
                        text=decode_output(bytes(buffer.head) + bytes(buffer.tail))
                    )
                ],
                details={"streaming": True, "bytes": buffer.total},
            )
        )

    async def pump() -> None:
        while True:
            chunk = await proc.stdout.read(READ_CHUNK)
            if not chunk:
                return
            buffer.add(chunk)
            emit_progress()

    read_task = asyncio.create_task(pump())
    abort_task = asyncio.create_task(abort.wait()) if abort is not None else None
    waiters = {read_task} | ({abort_task} if abort_task is not None else set())

    timed_out = aborted = False
    try:
        done, _ = await asyncio.wait(
            waiters, timeout=timeout_s, return_when=asyncio.FIRST_COMPLETED
        )
        if not done:
            timed_out = True
            await kill_process_group(proc)
        elif abort_task is not None and abort_task in done and not read_task.done():
            aborted = True
            await kill_process_group(proc)
        # Drain whatever the process managed to write.
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(read_task, timeout=DRAIN_TIMEOUT_S)
    finally:
        if abort_task is not None:
            abort_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await abort_task
        if not read_task.done():
            read_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await read_task
        if proc.returncode is None:
            await kill_process_group(proc)

    emit_progress(force=True)
    duration_ms = round((time.monotonic() - started) * 1000)
    exit_code = proc.returncode
    body = buffer.text()

    notes: list[str] = []
    if timed_out:
        notes.append(f"timed out after {timeout_s:g}s; process group killed")
    if aborted:
        notes.append("aborted; process group killed")
    header = f"exit code: {exit_code}"
    text = header + ("\n" + body if body else "")
    if notes:
        text += "\n[" + "; ".join(notes) + "]"

    return _text(
        text,
        exit_code=exit_code,
        timed_out=timed_out,
        aborted=aborted,
        truncated=buffer.truncated,
        duration_ms=duration_ms,
        cwd=str(cwd),
    )


BASH = AgentTool(
    name="bash",
    description=(
        "Run a shell command. stdout and stderr are merged; output is truncated "
        "to head+tail if very large. Runs non-interactively (pagers/prompts are "
        "disabled) and in an optional cwd. Report the exit code yourself when it "
        "matters. With async=true the command runs in the background and the "
        "wait tool collects its result."
    ),
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command to run."},
            "timeout_s": {
                "type": "number",
                "description": (
                    "Kill the command after this many seconds (default 120; "
                    "background jobs default to 600, clamped to 1-3600)."
                ),
            },
            "cwd": {
                "type": "string",
                "description": (
                    "Directory to run in (absolute, or relative to the current "
                    "working directory; default: the agent's cwd)."
                ),
            },
            "async": {
                "type": "boolean",
                "default": False,
                "description": (
                    "Start the command in the background and return a job id "
                    "immediately; use the wait tool to collect its output."
                ),
            },
        },
        "required": ["command"],
    },
    execute=_bash,
    approval="exec",
)
