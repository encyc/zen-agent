"""Minimal Model Context Protocol client (no official SDK).

Only the subset pyagent needs: JSON-RPC 2.0 over stdio subprocesses or the
Streamable HTTP transport, plus ``tools/list`` and ``tools/call``.
"""

from pyagent.mcp.client import (
    HttpTransport,
    McpCallResult,
    McpClient,
    McpError,
    McpTimeout,
    McpTool,
    StdioTransport,
)
from pyagent.mcp.config import McpServerConfig, load_mcp_config, make_transport

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
