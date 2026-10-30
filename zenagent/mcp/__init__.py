"""Minimal Model Context Protocol client (no official SDK).

Only the subset zenagent needs: JSON-RPC 2.0 over stdio subprocesses or the
Streamable HTTP transport, plus ``tools/list`` and ``tools/call``.
"""

from __future__ import annotations

from zenagent.mcp.client import (
    HttpTransport,
    McpCallResult,
    McpClient,
    McpError,
    McpTimeout,
    McpTool,
    StdioTransport,
)
from zenagent.mcp.config import McpServerConfig, load_mcp_config, make_transport

__all__ = [
    "HttpTransport",
    "McpCallResult",
    "McpClient",
    "McpError",
    "McpServerConfig",
    "McpTimeout",
    "McpTool",
    "StdioTransport",
    "load_mcp_config",
    "make_transport",
]
