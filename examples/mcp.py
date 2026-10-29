"""Start an MCP server and call one of its tools directly.

    uv run python examples/mcp.py
"""

from __future__ import annotations

import asyncio
import sys

from zenagent.mcp.config import McpServerConfig
from zenagent.mcp.manager import McpManager


async def main() -> int:
    config = McpServerConfig(
        name="echo",
        command=[sys.executable, "tests/fixtures/mcp_echo_server.py"],
    )
    manager = McpManager({"echo": config})
    try:
        warnings = await manager.start()
        for warning in warnings:
            print("warning:", warning)
        for status in manager.status():
            print(f"{status.name}: {status.state} ({len(status.tools)} tools)")
        result = await manager.call("echo", "echo", {"text": "hello"})
        print("call:", result.content[0]["text"])
        return 0 if not warnings else 1
    finally:
        await manager.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
