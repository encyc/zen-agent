"""LSP bridge: semantic code queries through a real language server.

Read-only actions (diagnostics / symbols / references / definition) plus a
``rename`` that returns the proposed workspace edit without applying it. Servers
are external binaries discovered on PATH (pyright, typescript-language-server);
a missing server is reported per action, never by silently removing the tool.

The client is a stdlib JSON-RPC implementation over stdio with LSP's
``Content-Length`` framing. One client per (binary, workspace root) is cached
for the process; ``shutdown_clients()`` releases them (tests and runtime close).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from zenagent.agent.loop import AgentTool, ToolResultAgent
from zenagent.types import TextContent

__all__ = [
    "LSP",
    "ServerSpec",
    "build_lsp_tool",
    "lsp_available",
    "server_for",
    "shutdown_clients",
]

#: Per-request deadline (pyright cold start + analysis can be slow).
_REQUEST_TIMEOUT_S = 20.0
#: How long to wait for the first publishDiagnostics after didOpen.
_DIAGNOSTICS_TIMEOUT_S = 8.0
#: Output cap for one action (the kernel budget is the safety net).
_MAX_OUTPUT_CHARS = 20_000

_DESCRIPTION = (
    "Semantic code intelligence through a language server (read-only): "
    "action=diagnostics|symbols|references|definition|rename. `path` is the "
    "file; references/definition/rename also need `line` (1-based) and "
    "`character` (0-based); rename needs `new_name`. Requires pyright "
    "(Python) or typescript-language-server (TS/JS) on PATH; a missing server "
    "is reported as an error."
)

_OPERATIONS = ("diagnostics", "symbols", "references", "definition", "rename")


@dataclass(frozen=True)
class ServerSpec:
    """One language server: binary + argv, and the extensions it owns."""

    name: str
    command: tuple[str, ...]
    extensions: tuple[str, ...]


SERVERS: tuple[ServerSpec, ...] = (
    ServerSpec(
        name="pyright",
        command=("pyright-langserver", "--stdio"),
        extensions=(".py", ".pyi"),
    ),
    ServerSpec(
        name="typescript",
        command=("typescript-language-server", "--stdio"),
        extensions=(".ts", ".tsx", ".js", ".jsx", ".mts", ".cts"),
    ),
)


def server_for(path: Path) -> ServerSpec | None:
    """The server that owns ``path``'s extension, or None."""
    suffix = path.suffix.lower()
    for spec in SERVERS:
        if suffix in spec.extensions:
            return spec
    return None


def lsp_available() -> bool:
    """Whether at least one configured server binary is installed."""
    return any(shutil.which(spec.command[0]) for spec in SERVERS)


def _text(body: str, *, is_error: bool = False, **details) -> ToolResultAgent:
    return ToolResultAgent(
        content=[TextContent(text=body)], details=details, is_error=is_error
    )


def _uri(path: Path) -> str:
    return path.resolve().as_uri()


def _path_of(uri: str) -> str:
    if uri.startswith("file://"):
        from urllib.parse import unquote, urlparse

        return unquote(urlparse(uri).path)
    return uri


def _workspace_root(path: Path) -> Path:
    """Nearest project marker above ``path``, else its parent."""
    markers = ("pyproject.toml", "setup.py", "setup.cfg", "package.json", "tsconfig.json")
    for parent in (path.parent, *path.parent.parents):
        if any((parent / marker).is_file() for marker in markers):
            return parent
    return path.parent


def _clip(body: str) -> tuple[str, bool]:
    if len(body) <= _MAX_OUTPUT_CHARS:
        return body, False
    return body[:_MAX_OUTPUT_CHARS] + "\n… (truncated)", True


class LspClient:
    """Minimal LSP client: initialize, request, notifications, diagnostics."""

    def __init__(self, spec: ServerSpec, root: Path) -> None:
        self.spec = spec
        self.root = root
        self.process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task | None = None
        self._next_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._diagnostics: dict[str, list] = {}
        self._diagnostic_events: dict[str, asyncio.Event] = {}
        self._closed = False

    async def start(self) -> None:
        if self.process is not None:
            return
        binary = shutil.which(self.spec.command[0])
        if binary is None:  # pragma: no cover - the tool checks this first
            raise FileNotFoundError(self.spec.command[0])
        self.process = await asyncio.create_subprocess_exec(
            binary,
            *self.spec.command[1:],
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env={**os.environ},
        )
        self._reader_task = asyncio.create_task(self._read_loop())
        await self.request(
            "initialize",
            {
                "processId": os.getpid(),
                "rootUri": _uri(self.root),
                "workspaceFolders": [
                    {"uri": _uri(self.root), "name": self.root.name or "workspace"}
                ],
                "capabilities": {
                    "textDocument": {
                        "documentSymbol": {"hierarchicalDocumentSymbolSupport": True},
                        "rename": {"prepareSupport": False},
                    }
                },
            },
            timeout=_REQUEST_TIMEOUT_S,
        )
        await self.notify("initialized", {})

    # -- wire --------------------------------------------------------------

    async def _send(self, message: dict) -> None:
        assert self.process is not None and self.process.stdin is not None
        body = json.dumps(message, ensure_ascii=False).encode("utf-8")
        self.process.stdin.write(
            f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body
        )
        await self.process.stdin.drain()

    async def _read_message(self) -> dict | None:
        assert self.process is not None and self.process.stdout is not None
        reader = self.process.stdout
        length: int | None = None
        while True:
            line = await reader.readline()
            if not line:
                return None
            header = line.decode("ascii", "replace").strip()
            if not header:
                break
            key, _, value = header.partition(":")
            if key.strip().lower() == "content-length":
                length = int(value.strip())
        if length is None:
            return None
        body = await reader.readexactly(length)
        return json.loads(body.decode("utf-8", "replace"))

    async def _read_loop(self) -> None:
        try:
            while not self._closed:
                message = await self._read_message()
                if message is None:
                    break
                await self._dispatch(message)
        finally:
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(ConnectionError("language server closed"))
            self._pending.clear()

    async def _dispatch(self, message: dict) -> None:
        if "id" in message and ("result" in message or "error" in message):
            future = self._pending.pop(message["id"], None)
            if future is not None and not future.done():
                if "error" in message:
                    future.set_exception(
                        RuntimeError(str((message["error"] or {}).get("message", "LSP error")))
                    )
                else:
                    future.set_result(message.get("result"))
            return
        method = message.get("method")
        if method == "textDocument/publishDiagnostics":
            params = message.get("params") or {}
            uri = str(params.get("uri", ""))
            self._diagnostics[uri] = list(params.get("diagnostics") or [])
            event = self._diagnostic_events.get(uri)
            if event is not None:
                event.set()
            return
        if "id" in message:
            # Server → client request: answer the ones that block real servers.
            result = None
            if method == "workspace/configuration":
                items = (message.get("params") or {}).get("items") or []
                result = [None] * len(items)
            elif method == "workspace/workspaceFolders":
                result = [
                    {"uri": _uri(self.root), "name": self.root.name or "workspace"}
                ]
            await self._send({"jsonrpc": "2.0", "id": message["id"], "result": result})

    async def request(
        self, method: str, params: dict, *, timeout: float = _REQUEST_TIMEOUT_S
    ) -> dict | list | None:
        if self._closed:
            raise ConnectionError("language server is closed")
        self._next_id += 1
        request_id = self._next_id
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        await self._send(
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        )
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except TimeoutError as exc:
            self._pending.pop(request_id, None)
            raise TimeoutError(f"{method} timed out after {timeout:g}s") from exc

    async def notify(self, method: str, params: dict) -> None:
        await self._send({"jsonrpc": "2.0", "method": method, "params": params})

    # -- document helpers --------------------------------------------------

    async def open_document(self, path: Path) -> None:
        text = path.read_text(encoding="utf-8", errors="replace")
        await self.notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": _uri(path),
                    "languageId": path.suffix.lstrip(".") or "plaintext",
                    "version": 1,
                    "text": text,
                }
            },
        )

    async def wait_diagnostics(self, path: Path, timeout: float) -> list:
        uri = _uri(path)
        if uri in self._diagnostics:
            return self._diagnostics[uri]
        event = self._diagnostic_events.setdefault(uri, asyncio.Event())
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except TimeoutError:
            return []
        return self._diagnostics.get(uri, [])

    async def wait_analyzed(self, path: Path, timeout: float) -> None:
        """Wait for the server's first publishDiagnostics for ``path``.

        Some servers (pyright) answer cross-file queries with partial results
        while analysis of a freshly opened document is still running. The
        first publishDiagnostics for the document is the completion signal.
        Best effort: on timeout the caller proceeds with whatever is ready.
        """
        uri = _uri(path)
        if uri in self._diagnostics:
            return
        event = self._diagnostic_events.setdefault(uri, asyncio.Event())
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(event.wait(), timeout=timeout)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        process, self.process = self.process, None
        if self._reader_task is not None:
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader_task
            self._reader_task = None
        if process is not None:
            if process.stdin is not None:
                process.stdin.close()
            if process.returncode is None:
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=3.0)
            except TimeoutError:
                process.kill()


_CLIENTS: dict[tuple[str, str], LspClient] = {}
_LOCK = asyncio.Lock()


async def _client_for(spec: ServerSpec, root: Path) -> LspClient:
    key = (spec.command[0], str(root))
    async with _LOCK:
        client = _CLIENTS.get(key)
        if client is None:
            client = LspClient(spec, root)
            await client.start()
            _CLIENTS[key] = client
        return client


async def shutdown_clients() -> None:
    """Close every cached client (tests and Runtime.close)."""
    clients = list(_CLIENTS.values())
    _CLIENTS.clear()
    for client in clients:
        await client.close()


# -- formatting ------------------------------------------------------------


def _format_location(location: dict) -> str:
    uri = str(location.get("uri") or "")
    path = _path_of(uri)
    span = location.get("range") or {}
    start = span.get("start") or {}
    line = int(start.get("line", 0)) + 1
    character = int(start.get("character", 0)) + 1
    return f"{path}:{line}:{character}"


def _symbol_kind(kind: int) -> str:
    kinds = {
        5: "class",
        6: "method",
        9: "constructor",
        12: "function",
        13: "variable",
        14: "constant",
        22: "enum",
        23: "struct",
        24: "event",
        26: "type",
    }
    return kinds.get(kind, f"kind{kind}")


def _format_symbols(symbols: list) -> list[str]:
    lines: list[str] = []

    def walk(items: list, depth: int = 0) -> None:
        for item in items:
            if not isinstance(item, dict):
                continue
            name = item.get("name", "?")
            kind = _symbol_kind(int(item.get("kind", 0)))
            range_ = item.get("range") or item.get("location") or {}
            start = range_.get("start") or range_.get("range", {}).get("start") or {}
            line = int(start.get("line", 0)) + 1 if start else 0
            lines.append(f"{'  ' * depth}{name}  {kind}  line {line}")
            children = item.get("children")
            if isinstance(children, list):
                walk(children, depth + 1)

    walk(symbols)
    return lines


def _format_diagnostics(diagnostics: list, path: Path) -> list[str]:
    severities = {1: "error", 2: "warning", 3: "info", 4: "hint"}
    lines: list[str] = []
    for item in diagnostics:
        if not isinstance(item, dict):
            continue
        span = item.get("range") or {}
        start = span.get("start") or {}
        line = int(start.get("line", 0)) + 1
        character = int(start.get("character", 0)) + 1
        severity = severities.get(int(item.get("severity", 1)), "error")
        message = " ".join(str(item.get("message", "")).split())
        source = item.get("source")
        suffix = f" ({source})" if source else ""
        lines.append(f"{path}:{line}:{character}: {severity}: {message}{suffix}")
    return lines


def _format_workspace_edit(edit: dict) -> list[str]:
    lines: list[str] = []
    changes = edit.get("changes")
    if isinstance(changes, dict):
        for uri, edits in changes.items():
            lines.append(f"{_path_of(uri)}: {len(edits)} edit(s)")
            for item in edits[:20]:
                span = (item.get("range") or {}).get("start") or {}
                line = int(span.get("line", 0)) + 1
                lines.append(f"  line {line}: → {item.get('newText', '')!r}")
    document_changes = edit.get("documentChanges")
    if isinstance(document_changes, list):
        for item in document_changes:
            if not isinstance(item, dict):
                continue
            if "edits" in item:  # TextDocumentEdit (pyright's shape, kind-less)
                uri = str((item.get("textDocument") or {}).get("uri", ""))
                edits = item.get("edits") or []
                lines.append(f"{_path_of(uri)}: {len(edits)} edit(s)")
                for change in edits[:20]:
                    span = (change.get("range") or {}).get("start") or {}
                    line = int(span.get("line", 0)) + 1
                    lines.append(f"  line {line}: → {change.get('newText', '')!r}")
            elif item.get("kind") == "rename":
                lines.append(
                    f"rename file: {_path_of(str(item.get('oldUri')))} → "
                    f"{_path_of(str(item.get('newUri')))}"
                )
    return lines or ["(empty workspace edit)"]


# -- tool ------------------------------------------------------------------


async def _lsp(
    tool_call_id: str, args: dict, abort, on_update=None
) -> ToolResultAgent:
    action = str(args.get("action") or "").strip()
    if action not in _OPERATIONS:
        return _text(
            f"unknown action {action!r}; expected one of {', '.join(_OPERATIONS)}",
            is_error=True,
        )
    raw_path = str(args.get("path") or "").strip()
    if not raw_path:
        return _text("path is required", is_error=True)
    path = Path(raw_path).expanduser().resolve()
    if not path.is_file():
        return _text(f"file not found: {path}", is_error=True)
    spec = server_for(path)
    if spec is None:
        return _text(
            f"no language server configured for {path.suffix or '(no extension)'}",
            is_error=True,
        )
    if shutil.which(spec.command[0]) is None:
        return _text(
            f"language server {spec.command[0]!r} is not installed; install it to "
            f"use {action} on {path.suffix} files",
            is_error=True,
        )

    root = _workspace_root(path)
    try:
        client = await _client_for(spec, root)
    except (OSError, ConnectionError, TimeoutError) as exc:
        return _text(f"cannot start {spec.name}: {exc}", is_error=True)
    try:
        await client.open_document(path)
        if action == "diagnostics":
            diagnostics = await client.wait_diagnostics(path, _DIAGNOSTICS_TIMEOUT_S)
            lines = _format_diagnostics(diagnostics, path)
            if not lines:
                return _text(
                    "no diagnostics (server reported none yet, or the file is clean)",
                    action=action,
                    count=0,
                )
            body, truncated = _clip("\n".join(lines))
            return _text(body, action=action, count=len(lines), truncated=truncated)

        position = {
            "textDocument": {"uri": _uri(path)},
            "position": {
                "line": max(0, int(args.get("line", 1)) - 1),
                "character": max(0, int(args.get("character", 0))),
            },
        }
        if action == "symbols":
            result = await client.request("textDocument/documentSymbol", {
                "textDocument": {"uri": _uri(path)}
            })
            lines = _format_symbols(result or [])
            body = "\n".join(lines) or "no symbols"
            body, truncated = _clip(body)
            return _text(body, action=action, count=len(lines), truncated=truncated)

        if action in ("references", "definition"):
            method = (
                "textDocument/references"
                if action == "references"
                else "textDocument/definition"
            )
            params = dict(position)
            if action == "references":
                params["context"] = {"includeDeclaration": True}
            result = await client.request(method, params)
            locations = result if isinstance(result, list) else ([result] if result else [])
            lines = [_format_location(item) for item in locations if isinstance(item, dict)]
            body = "\n".join(lines) or "no locations"
            body, truncated = _clip(body)
            return _text(body, action=action, count=len(lines), truncated=truncated)

        # rename: return the proposed edit; applying it stays the model's job.
        new_name = str(args.get("new_name") or "").strip()
        if not new_name:
            return _text("rename needs new_name", is_error=True)
        # Servers answer rename with partial edits mid-analysis; wait for the
        # analysis-complete signal so the edit covers every referencing file.
        await client.wait_analyzed(path, _DIAGNOSTICS_TIMEOUT_S)
        params = dict(position)
        params["newName"] = new_name
        edit = await client.request("textDocument/rename", params)
        lines = _format_workspace_edit(edit or {})
        body, truncated = _clip("\n".join(lines))
        return _text(body, action=action, count=len(lines), truncated=truncated)
    except TimeoutError as exc:
        return _text(f"{action} timed out: {exc}", is_error=True)
    except (ConnectionError, RuntimeError) as exc:
        return _text(f"{action} failed: {exc}", is_error=True)


LSP = AgentTool(
    name="lsp",
    description=_DESCRIPTION,
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": list(_OPERATIONS),
                "description": "Semantic operation to run.",
            },
            "path": {"type": "string", "description": "File to query."},
            "line": {
                "type": "integer",
                "description": "1-based line for references/definition/rename.",
            },
            "character": {
                "type": "integer",
                "description": "0-based character offset (default 0).",
            },
            "new_name": {
                "type": "string",
                "description": "New identifier for rename.",
            },
        },
        "required": ["action", "path"],
    },
    execute=_lsp,
    approval="read",
    timeout_s=_REQUEST_TIMEOUT_S + 10.0,
)


def build_lsp_tool() -> AgentTool:
    """Always registered: a missing server is reported per action, not hidden."""
    return LSP
