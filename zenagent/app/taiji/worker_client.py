"""A JSONL client for one ``zen rpc`` worker subprocess.

The client is transport-agnostic: production spawns a real subprocess, tests
inject a loopback transport wired to an in-process ``RpcServer`` — the
framing, id correlation and event delivery are identical either way.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Protocol

EVENT_CALLBACK = Callable[[dict], None]
LINE_CALLBACK = Callable[[str], Awaitable[None]]


def worker_command(cwd: Path, *, model: str | None = None) -> list[str]:
    """How to spawn a worker: the installed ``zen``, else this interpreter.

    ``model`` routes the worker to a strong model while the Router session
    stays light (spec §9); ``None`` inherits the provider default.
    """
    zen = shutil.which("zen")
    head = [zen] if zen else [sys.executable, "-m", "zenagent"]
    command = [*head, "rpc", "--cwd", str(cwd), "--yes"]
    if model:
        command += ["--model", model]
    return command


class WorkerTransport(Protocol):
    async def start(self, on_line: LINE_CALLBACK) -> None: ...

    async def send_line(self, line: str) -> None: ...

    async def stop(self) -> None: ...


class SubprocessTransport:
    """One ``zen rpc`` child process, line-pumped over stdio."""

    def __init__(self, command: list[str], cwd: Path) -> None:
        self._command = command
        self._cwd = cwd
        self._process: asyncio.subprocess.Process | None = None
        self._pump: asyncio.Task[None] | None = None

    async def start(self, on_line: LINE_CALLBACK) -> None:
        self._process = await asyncio.create_subprocess_exec(
            *self._command,
            cwd=str(self._cwd),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        self._pump = asyncio.create_task(self._pump(on_line))

    async def _pump(self, on_line: LINE_CALLBACK) -> None:
        assert self._process is not None and self._process.stdout is not None
        async for raw in self._process.stdout:
            await on_line(raw.decode("utf-8").rstrip("\n"))

    async def send_line(self, line: str) -> None:
        assert self._process is not None and self._process.stdin is not None
        self._process.stdin.write((line + "\n").encode("utf-8"))
        await self._process.stdin.drain()

    async def stop(self) -> None:
        if self._pump is not None:
            self._pump.cancel()
        if self._process is None:
            return
        try:
            self._process.terminate()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(self._process.wait(), 5.0)
        except TimeoutError:
            self._process.kill()
            await self._process.wait()


class WorkerRpcClient:
    """Speak the zen rpc protocol to one worker: hello, resume, prompt, stop."""

    def __init__(self, *, transport: WorkerTransport | None = None) -> None:
        self.transport: WorkerTransport | None = transport
        self.last_result: dict | None = None
        self._on_event: EVENT_CALLBACK = lambda event: None
        self._next_id = 0
        self._pending: dict[str, asyncio.Future[dict]] = {}

    def _frame(self, payload: dict) -> dict:
        self._next_id += 1
        return {"id": f"w{self._next_id}", **payload}

    async def _request(self, payload: dict, timeout_s: float) -> dict:
        assert self.transport is not None
        frame = self._frame(payload)
        future: asyncio.Future[dict] = asyncio.get_running_loop().create_future()
        self._pending[frame["id"]] = future
        await self.transport.send_line(json.dumps(frame, ensure_ascii=False))
        try:
            return await asyncio.wait_for(future, timeout_s)
        finally:
            self._pending.pop(frame["id"], None)

    async def _on_line(self, line: str) -> None:
        if not line.strip():
            return
        try:
            message = json.loads(line)
        except ValueError:
            return
        if message.get("type") == "event":
            self._on_event(message.get("event", {}))
            return
        identifier = message.get("id")
        if isinstance(identifier, str) and identifier in self._pending:
            self.last_result = message
            future = self._pending.pop(identifier)
            if not future.done():
                future.set_result(message)

    async def start(self, cwd: Path, *, on_event: EVENT_CALLBACK) -> None:
        if self.transport is None:
            self.transport = SubprocessTransport(worker_command(cwd), cwd)
        self._on_event = on_event
        await self.transport.start(self._on_line)
        hello = await self._request({"type": "hello"}, 30.0)
        if hello.get("ok") is not True:
            raise RuntimeError(f"worker handshake failed: {hello}")
        if hello.get("protocol_version") != 2:
            raise RuntimeError(
                f"worker protocol mismatch: {hello.get('protocol_version')}"
            )

    async def resume(self, session_path: str) -> dict:
        return await self._request(
            {"type": "session", "op": "resume", "path": session_path}, 60.0
        )

    async def prompt(self, text: str, *, timeout_s: float = 1800.0) -> dict:
        answer = await self._request({"type": "prompt", "text": text}, timeout_s)
        if answer.get("type") == "error":
            raise RuntimeError(f"worker prompt failed: {answer.get('message')}")
        return answer

    async def stop(self) -> None:
        if self.transport is None:
            return
        try:
            await self.transport.send_line(
                json.dumps({"type": "shutdown", "id": "w-stop"})
            )
        except (OSError, RuntimeError, AssertionError):
            pass  # the child may already be gone; terminate below regardless
        await self.transport.stop()
        self.transport = None
