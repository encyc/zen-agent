"""Lifecycle for configured MCP servers — including servers that arrive late.

A server that is down when the session starts must not be permanently dead: it
is kept in a ``failed`` state, retried later (on demand or before the next
prompt), and when it finally connects its tools are discovered and swapped into
the agent's tool list. That is the difference between "MCP at startup" and
"usable MCP".
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import time
from dataclasses import dataclass, field

from zenagent.agent.loop import AgentTool, ToolResultAgent
from zenagent.mcp.client import McpCallResult, McpClient, McpError, McpTool
from zenagent.mcp.config import McpServerConfig, make_transport
from zenagent.types import ImageContent, TextContent

__all__ = ["McpManager", "McpStatus", "tool_name_for"]

#: Prefix for tools contributed by MCP servers (keeps built-ins unambiguous).
TOOL_PREFIX = "mcp__"

_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")


def tool_name_for(server: str, tool: str) -> str:
    """``mcp__<server>__<tool>`` with anything the model can't read sanitized."""
    return f"{TOOL_PREFIX}{_UNSAFE.sub('_', server)}__{_UNSAFE.sub('_', tool)}"


@dataclass
class McpStatus:
    """Snapshot of one server for ``/mcp`` and diagnostics."""

    name: str
    state: str  # "connected" | "failed"
    tools: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class _Server:
    config: McpServerConfig
    client: McpClient | None = None
    tools: list[McpTool] = field(default_factory=list)
    error: str | None = None
    last_attempt: float = 0.0


class McpManager:
    """Owns every configured server's connection and derived tool definitions."""

    def __init__(
        self,
        configs: dict[str, McpServerConfig],
        *,
        timeout_s: float = 30.0,
        retry_interval_s: float = 10.0,
    ) -> None:
        self._servers = {name: _Server(config=config) for name, config in configs.items()}
        self._timeout_s = timeout_s
        self._retry_interval_s = retry_interval_s

    # -- introspection ------------------------------------------------------

    @property
    def names(self) -> list[str]:
        return list(self._servers)

    @property
    def connected_names(self) -> list[str]:
        return [name for name, server in self._servers.items() if server.client is not None]

    def status(self) -> list[McpStatus]:
        return [
            McpStatus(
                name=name,
                state="connected" if server.client is not None else "failed",
                tools=[tool.name for tool in server.tools],
                error=server.error,
            )
            for name, server in self._servers.items()
        ]

    # -- lifecycle ----------------------------------------------------------

    async def start(self) -> list[str]:
        """Connect every server; returns human-readable warnings for failures."""
        warnings = await asyncio.gather(*(self._connect(name) for name in self._servers))
        return [warning for warning in warnings if warning]

    async def retry_failed(self, *, force: bool = False) -> list[str]:
        """Retry servers that are not connected.

        Without ``force`` a server is only retried every ``retry_interval_s`` so
        a dead server does not add a connection timeout to every prompt.
        """
        warnings: list[str] = []
        now = time.monotonic()
        for name, server in self._servers.items():
            if server.client is not None:
                continue
            if not force and now - server.last_attempt < self._retry_interval_s:
                continue
            warning = await self._connect(name)
            if warning:
                warnings.append(warning)
        return warnings

    async def refresh_tools(self) -> bool:
        """Re-list tools on connected servers; True when the set changed."""
        before = [tool.name for tool in self.tool_defs()]
        for server in self._servers.values():
            if server.client is None:
                continue
            try:
                tools = await server.client.list_tools()
            except McpError as exc:
                server.error = f"{type(exc).__name__}: {exc}"
                continue
            server.tools = tools
        return before != [tool.name for tool in self.tool_defs()]

    async def close(self) -> None:
        for server in self._servers.values():
            client, server.client = server.client, None
            if client is not None:
                await client.close()

    async def _connect(self, name: str) -> str | None:
        server = self._servers[name]
        if server.client is not None:
            return None
        server.last_attempt = time.monotonic()
        client: McpClient | None = None
        try:
            client = McpClient(name, make_transport(server.config), timeout_s=self._timeout_s)
            await client.start()
            server.tools = await client.list_tools()
        except (McpError, OSError, ValueError) as exc:
            server.error = f"{type(exc).__name__}: {exc}"
            server.tools = []
            if client is not None:
                with contextlib.suppress(Exception):
                    await client.close()
            return f"{name}: {server.error}"
        server.client = client
        server.error = None
        return None

    # -- tools --------------------------------------------------------------

    def tool_defs(self) -> list[AgentTool]:
        """AgentTools for every tool of every connected server."""
        defs: list[AgentTool] = []
        for name, server in self._servers.items():
            if server.client is None:
                continue
            for tool in server.tools:
                defs.append(self._make_tool(name, tool))
        return defs

    def _make_tool(self, server_name: str, tool: McpTool) -> AgentTool:
        manager = self

        async def execute(call_id, args, abort, on_update=None) -> ToolResultAgent:
            try:
                result = await manager.call(server_name, tool.name, args)
            except McpError as exc:
                return ToolResultAgent(content=[TextContent(text=str(exc))], is_error=True)
            return _to_result(result)

        description = tool.description or f"{tool.name} (via {server_name})"
        return AgentTool(
            name=tool_name_for(server_name, tool.name),
            description=f"[{server_name}] {description}",
            parameters=tool.input_schema,
            execute=execute,
        )

    async def call(self, server: str, tool: str, arguments: dict) -> McpCallResult:
        state = self._servers.get(server)
        if state is None:
            raise McpError(f"unknown MCP server: {server}")
        if state.client is None:
            raise McpError(
                f"MCP server {server!r} is not connected"
                + (f" ({state.error})" if state.error else "")
            )
        return await state.client.call_tool(tool, arguments, timeout_s=self._timeout_s)


def _to_result(result: McpCallResult) -> ToolResultAgent:
    """Map MCP content blocks onto our content model."""
    blocks: list = []
    for item in result.content:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind == "text":
            blocks.append(TextContent(text=str(item.get("text", ""))))
        elif kind == "image":
            blocks.append(
                ImageContent(
                    data=str(item.get("data", "")),
                    mime_type=str(item.get("mimeType") or "image/png"),
                )
            )
        elif kind == "resource":
            resource = item.get("resource") or {}
            text = resource.get("text")
            if text is not None:
                blocks.append(TextContent(text=str(text)))
            else:
                blocks.append(TextContent(text=f"[resource {resource.get('uri', '')}]"))
    if not blocks:
        blocks = [TextContent(text="(empty MCP result)")]
    return ToolResultAgent(content=blocks, is_error=result.is_error)
