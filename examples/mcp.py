"""Start an MCP server and call one of its tools -- no model involved.

Uses the echo server in ``tests/fixtures`` (a stdio JSON-RPC stub), so this runs
offline but needs a source checkout. For a real server, point ``McpServerConfig``
at any command or URL -- ``McpManager.tool_defs()`` then turns its tools into
``AgentTool`` objects the model can call like built-ins.

    uv run python examples/mcp.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from zenagent.mcp.client import McpError
from zenagent.mcp.config import McpServerConfig
from zenagent.mcp.manager import McpManager

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"


async def main() -> int:
    config = McpServerConfig(
        name="echo",
        command=[sys.executable, str(FIXTURES / "mcp_echo_server.py")],
    )
    manager = McpManager({"echo": config}, timeout_s=10.0)
    try:
        for warning in await manager.start():
            print("warning:", warning, file=sys.stderr)
        for status in manager.status():
            tools = ", ".join(status.tools) or "none"
            print(f"{status.name}: {status.state} -- tools: {tools}")
        if "echo" not in manager.connected_names:
            print("echo: not connected", file=sys.stderr)
            return 1

        try:
            result = await manager.call("echo", "echo", {"text": "hello"})
        except McpError as exc:  # unknown server, dead transport, timeout, ...
            print("call failed:", exc, file=sys.stderr)
            return 1

        text = "".join(
            block.get("text", "")
            for block in result.content
            if block.get("type") == "text"
        )
        print(f"call: {text}" + (" (error)" if result.is_error else ""))
        print("agent tools:", ", ".join(tool.name for tool in manager.tool_defs()))
        return 0 if text == "echo:hello" and not result.is_error else 1
    finally:
        await manager.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
