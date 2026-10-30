"""JSON-RPC 2.0 client for MCP servers.

Two transports, one interface:

* :class:`StdioTransport` launches the server as a child process and exchanges
  newline-delimited JSON on its stdin/stdout. The reader runs in its own task so
  requests never block on unrelated notifications.
* :class:`HttpTransport` speaks Streamable HTTP: a POST per message, whose
  answer is either a JSON body or a single SSE ``data:`` event.

Failure policy matches the rest of the kernel: transport problems surface as
:class:`McpTimeout` / :class:`McpError`, which the tool layer converts into
``is_error`` results.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

__all__ = [
    "DEFAULT_PROTOCOL_VERSION",
    "HttpTransport",
    "McpCallResult",
    "McpClient",
    "McpError",
    "McpTimeout",
    "McpTool",
    "StdioTransport",
]

DEFAULT_PROTOCOL_VERSION = "2024-11-05"

#: Lines of server stderr kept for error messages.
_MAX_STDERR_LINES = 20


class McpError(Exception):
    """A JSON-RPC error, a broken transport, or a protocol violation."""


class McpTimeout(McpError):
    """The server did not answer in time."""


@dataclass
class McpTool:
    """One tool advertised by a server."""

    name: str
    description: str
    input_schema: dict


@dataclass
class McpCallResult:
    """Result of ``tools/call``: raw content blocks plus the error flag."""

    content: list[dict] = field(default_factory=list)
    is_error: bool = False


class Transport(Protocol):
    """What :class:`McpClient` needs from a wire."""

    async def start(self) -> None: ...
    async def request(self, method: str, params: dict | None, timeout_s: float) -> dict: ...
    async def notify(self, method: str, params: dict | None = None) -> None: ...
    async def close(self) -> None: ...


# ---------------------------------------------------------------------------
# stdio
# ---------------------------------------------------------------------------


class StdioTransport:
    """Newline-delimited JSON-RPC over a child process."""

    def __init__(
        self,
        command: list[str],
        env: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> None:
        if not command:
            raise ValueError("stdio MCP server needs a command")
        self.command = list(command)
        self.env = env or {}
        self.cwd = cwd
        self._process: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._next_id = 0
        self._closed = False
        self._stderr: list[str] = []
        self._read_error: str | None = None

    async def start(self) -> None:
        if self._process is not None:
            return
        environment = {**os.environ, **self.env}
        self._process = await asyncio.create_subprocess_exec(
            *self.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=environment,
            cwd=self.cwd,
        )
        self._reader = asyncio.create_task(self._read_loop())
        self._stderr_task = asyncio.create_task(self._drain_stderr())

    async def _drain_stderr(self) -> None:
        assert self._process is not None and self._process.stderr is not None
        while True:
            line = await self._process.stderr.readline()
            if not line:
                return
            self._stderr.append(line.decode("utf-8", "replace").rstrip())
            if len(self._stderr) > _MAX_STDERR_LINES:
                del self._stderr[0]

    async def _read_loop(self) -> None:
        assert self._process is not None and self._process.stdout is not None
        stdout = self._process.stdout
        while True:
            line = await stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", "replace").strip()
            if not text:
                continue
            try:
                message = json.loads(text)
            except ValueError:
                continue  # servers sometimes log to stdout; ignore junk
            message_id = message.get("id")
            if message_id is None or message_id not in self._pending:
                continue  # notification or unknown id
            future = self._pending.pop(message_id)
            if future.done():
                continue
            if "error" in message:
                error = message["error"] or {}
                future.set_exception(
                    McpError(
                        f"rpc error {error.get('code')}: {error.get('message', 'unknown')}"
                    )
                )
            else:
                future.set_result(message.get("result") or {})
        if self._process is not None and self._process.returncode is None:
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await asyncio.wait_for(self._process.wait(), timeout=2.0)
        self._read_error = self._describe_exit()
        self._fail_pending(self._read_error)

    def _describe_exit(self) -> str:
        code = self._process.returncode if self._process is not None else None
        detail = f"server exited (code {code})" if code is not None else "server closed stdout"
        if self._stderr:
            detail += f": {self._stderr[-1]}"
        return detail

    def _fail_pending(self, reason: str) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(McpError(reason))
        self._pending.clear()

    async def request(self, method: str, params: dict | None, timeout_s: float) -> dict:
        if self._closed or self._process is None or self._process.stdin is None:
            raise McpError(self._read_error or "transport is closed")
        self._next_id += 1
        request_id = self._next_id
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._pending[request_id] = future
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            payload["params"] = params
        try:
            self._process.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
            await self._process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as exc:
            self._pending.pop(request_id, None)
            raise McpError(f"cannot write to MCP server: {exc}") from exc
        try:
            return await asyncio.wait_for(future, timeout_s)
        except (TimeoutError, asyncio.TimeoutError) as exc:
            self._pending.pop(request_id, None)
            raise McpTimeout(f"{method} timed out after {timeout_s}s") from exc

    async def notify(self, method: str, params: dict | None = None) -> None:
        if self._closed or self._process is None or self._process.stdin is None:
            raise McpError(self._read_error or "transport is closed")
        payload: dict = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        self._process.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
        await self._process.stdin.drain()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._fail_pending("transport closed")
        for task in (self._reader, self._stderr_task):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        process = self._process
        if process is None:
            return
        if process.stdin is not None:
            with contextlib.suppress(Exception):
                process.stdin.close()
        if process.returncode is None:
            try:
                await asyncio.wait_for(process.wait(), timeout=3.0)
            except (TimeoutError, asyncio.TimeoutError):
                with contextlib.suppress(ProcessLookupError):
                    process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=3.0)
                except (TimeoutError, asyncio.TimeoutError):
                    with contextlib.suppress(ProcessLookupError):
                        process.kill()
                    await process.wait()


# ---------------------------------------------------------------------------
# Streamable HTTP
# ---------------------------------------------------------------------------


class HttpTransport:
    """One POST per JSON-RPC message; JSON or SSE response."""

    def __init__(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        client: httpx.AsyncClient | None = None,
        timeout_s: float = 30.0,
    ) -> None:
        self.url = url
        self.headers = {"accept": "application/json, text/event-stream", **(headers or {})}
        self._client = client
        self._owns_client = client is None
        self._timeout_s = timeout_s
        self._next_id = 0
        self._session_id: str | None = None
        self._closed = False

    async def start(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(self._timeout_s))

    def _request_headers(self) -> dict[str, str]:
        headers = dict(self.headers)
        if self._session_id:
            headers["mcp-session-id"] = self._session_id
        headers["mcp-protocol-version"] = DEFAULT_PROTOCOL_VERSION
        return headers

    async def request(self, method: str, params: dict | None, timeout_s: float) -> dict:
        if self._closed:
            raise McpError("transport is closed")
        if self._client is None:
            await self.start()
        assert self._client is not None
        self._next_id += 1
        payload: dict = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        if params is not None:
            payload["params"] = params
        try:
            response = await self._client.post(
                self.url, headers=self._request_headers(), json=payload, timeout=timeout_s
            )
        except httpx.TimeoutException as exc:
            raise McpTimeout(f"{method} timed out after {timeout_s}s") from exc
        except httpx.HTTPError as exc:
            raise McpError(f"http transport error: {exc}") from exc

        session_id = response.headers.get("mcp-session-id")
        if session_id:
            self._session_id = session_id

        if response.status_code >= 400:
            raise McpError(f"http {response.status_code}: {response.text[:200]}")

        message = self._parse_message(response, self._next_id)
        if "error" in message:
            error = message["error"] or {}
            raise McpError(
                f"rpc error {error.get('code')}: {error.get('message', 'unknown')}"
            )
        return message.get("result") or {}

    @staticmethod
    def _parse_message(response: httpx.Response, request_id: int) -> dict:
        content_type = response.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            for line in response.text.splitlines():
                if not line.startswith("data:"):
                    continue
                payload = line[len("data:") :].strip()
                if not payload or payload == "[DONE]":
                    continue
                try:
                    message = json.loads(payload)
                except ValueError:
                    continue
                if message.get("id") == request_id:
                    return message
            raise McpError("SSE response contained no matching JSON-RPC message")
        try:
            return response.json()
        except ValueError as exc:
            raise McpError("response was not valid JSON") from exc

    async def notify(self, method: str, params: dict | None = None) -> None:
        if self._closed:
            raise McpError("transport is closed")
        if self._client is None:
            await self.start()
        assert self._client is not None
        payload: dict = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        with contextlib.suppress(httpx.HTTPError):
            await self._client.post(
                self.url, headers=self._request_headers(), json=payload, timeout=self._timeout_s
            )

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class McpClient:
    """Handshake, list tools, call tools — the three things zenagent needs."""

    def __init__(self, name: str, transport: Transport, timeout_s: float = 30.0) -> None:
        self.name = name
        self.transport = transport
        self.timeout_s = timeout_s
        self.server_info: dict[str, Any] = {}
        self.protocol_version: str | None = None
        self._started = False

    async def start(self) -> None:
        await self.transport.start()
        result = await self.transport.request(
            "initialize",
            {
                "protocolVersion": DEFAULT_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "zenagent", "version": "0.1.0"},
            },
            self.timeout_s,
        )
        self.server_info = result.get("serverInfo") or {}
        self.protocol_version = result.get("protocolVersion")
        await self.transport.notify("notifications/initialized")
        self._started = True

    async def list_tools(self) -> list[McpTool]:
        result = await self.transport.request("tools/list", {}, self.timeout_s)
        tools: list[McpTool] = []
        for raw in result.get("tools") or []:
            if not isinstance(raw, dict) or not raw.get("name"):
                continue
            tools.append(
                McpTool(
                    name=str(raw["name"]),
                    description=str(raw.get("description") or ""),
                    input_schema=raw.get("inputSchema") or {"type": "object"},
                )
            )
        return tools

    async def call_tool(
        self, name: str, arguments: dict, timeout_s: float | None = None
    ) -> McpCallResult:
        result = await self.transport.request(
            "tools/call",
            {"name": name, "arguments": arguments},
            timeout_s if timeout_s is not None else self.timeout_s,
        )
        content = result.get("content")
        return McpCallResult(
            content=content if isinstance(content, list) else [],
            is_error=bool(result.get("isError")),
        )

    async def close(self) -> None:
        await self.transport.close()

    async def __aenter__(self) -> "McpClient":  # noqa: PYI034, UP037
        await self.start()
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.close()
