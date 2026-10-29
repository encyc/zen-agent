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
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import time

from zenagent.agent.loop import AgentTool, ToolResultAgent
from zenagent.events import AbortToken
from zenagent.types import TextContent

__all__ = ["BASH", "MAX_OUTPUT_BYTES"]

#: Total output kept before head/tail truncation kicks in.
MAX_OUTPUT_BYTES = 30_000
#: Split of the kept bytes between the head and the tail.
_HEAD_BYTES = MAX_OUTPUT_BYTES // 2
_TAIL_BYTES = MAX_OUTPUT_BYTES - _HEAD_BYTES
#: Read size from the pipe.
_READ_CHUNK = 8192
#: Minimum accumulated bytes between two progress updates.
_UPDATE_BYTES = 4096
#: Minimum seconds between two progress updates.
_UPDATE_INTERVAL_S = 0.5
#: Grace period for the read loop to drain after a kill.
_DRAIN_TIMEOUT_S = 5.0


def _text(text: str, *, is_error: bool = False, **details) -> ToolResultAgent:
    return ToolResultAgent(
        content=[TextContent(text=text)], details=details, is_error=is_error
    )


def _decode(data: bytes) -> str:
    """Decode process output leniently: agents must not crash on odd bytes."""
    return data.decode("utf-8", errors="replace")


class _OutputBuffer:
    """Bounded head+tail buffer for process output."""

    def __init__(self) -> None:
        self.head = bytearray()
        self.tail = bytearray()
        self.total = 0

    def add(self, chunk: bytes) -> None:
        self.total += len(chunk)
        if len(self.head) < _HEAD_BYTES:
            take = min(len(chunk), _HEAD_BYTES - len(self.head))
            self.head.extend(chunk[:take])
            chunk = chunk[take:]
        if chunk:
            self.tail.extend(chunk)
            if len(self.tail) > _TAIL_BYTES:
                del self.tail[: len(self.tail) - _TAIL_BYTES]

    @property
    def truncated(self) -> bool:
        return self.total > MAX_OUTPUT_BYTES

    def text(self) -> str:
        if not self.truncated:
            return _decode(bytes(self.head) + bytes(self.tail))
        dropped = self.total - len(self.head) - len(self.tail)
        return (
            _decode(bytes(self.head))
            + f"\n... [truncated {dropped} bytes of output] ...\n"
            + _decode(bytes(self.tail))
        )


async def _kill_process_group(proc: asyncio.subprocess.Process) -> None:
    """Kill the command *and its children* (start_new_session gave us a pgid)."""
    if proc.returncode is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):  # already gone / not group leader
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
    with contextlib.suppress(asyncio.TimeoutError, ProcessLookupError):
        await asyncio.wait_for(proc.wait(), timeout=5.0)


async def _bash(
    tool_call_id: str, args: dict, abort: AbortToken | None, on_update=None
) -> ToolResultAgent:
    command = args["command"]
    timeout_s = float(args.get("timeout_s") or 120.0)
    started = time.monotonic()

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            stdin=asyncio.subprocess.DEVNULL,
            cwd=os.getcwd(),
            start_new_session=True,  # own process group, so we can kill children
        )
    except OSError as exc:
        return _text(f"cannot start command: {exc}", is_error=True)

    assert proc.stdout is not None
    buffer = _OutputBuffer()
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
                content=[TextContent(text=_decode(bytes(buffer.head) + bytes(buffer.tail)))],
                details={"streaming": True, "bytes": buffer.total},
            )
        )

    async def pump() -> None:
        while True:
            chunk = await proc.stdout.read(_READ_CHUNK)
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
            await _kill_process_group(proc)
        elif abort_task is not None and abort_task in done and not read_task.done():
            aborted = True
            await _kill_process_group(proc)
        # Drain whatever the process managed to write.
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(read_task, timeout=_DRAIN_TIMEOUT_S)
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
            await _kill_process_group(proc)

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
    )


BASH = AgentTool(
    name="bash",
    description=(
        "Run a shell command in the working directory. stdout and stderr are "
        "merged; output is truncated to head+tail if very large. Report the "
        "exit code yourself when it matters."
    ),
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command to run."},
            "timeout_s": {
                "type": "number",
                "description": "Kill the command after this many seconds (default 120).",
            },
        },
        "required": ["command"],
    },
    execute=_bash,
)
