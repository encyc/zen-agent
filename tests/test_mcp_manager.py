"""MCP manager: late-starting servers, tool exposure, error isolation."""

import sys
from pathlib import Path

import pytest

from zenagent.mcp.config import McpServerConfig
from zenagent.mcp.manager import McpManager, tool_name_for
from zenagent.types import ImageContent, TextContent

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ECHO = FIXTURES / "mcp_echo_server.py"
FLAKY = FIXTURES / "mcp_flaky_server.py"


def stdio(name: str, script: Path, env: dict[str, str] | None = None) -> McpServerConfig:
    return McpServerConfig(
        name=name, command=[sys.executable, str(script)], env=env or {}
    )


def tool_names(manager: McpManager) -> list[str]:
    return [tool.name for tool in manager.tool_defs()]


async def test_late_starting_server_becomes_usable_after_retry(tmp_path):
    """The zcode problem: down at session start, must work once it is up."""
    sentinel = tmp_path / "ready"
    manager = McpManager(
        {"flaky": stdio("flaky", FLAKY, {"MCP_READY_FILE": str(sentinel)})},
        retry_interval_s=0,
    )
    try:
        warnings = await manager.start()
        assert warnings and "flaky" in warnings[0]
        assert manager.status()[0].state == "failed"
        assert tool_names(manager) == []

        # the server "comes up" later
        sentinel.write_text("ready")
        warnings = await manager.retry_failed()
        assert warnings == []
        status = manager.status()[0]
        assert status.state == "connected"
        assert "echo" in status.tools
        assert "mcp__flaky__echo" in tool_names(manager)
    finally:
        await manager.close()


async def test_failed_server_is_not_retried_every_prompt(tmp_path):
    sentinel = tmp_path / "never"
    manager = McpManager(
        {"flaky": stdio("flaky", FLAKY, {"MCP_READY_FILE": str(sentinel)})},
        retry_interval_s=60,
    )
    try:
        await manager.start()
        first_attempt = manager._servers["flaky"].last_attempt
        assert await manager.retry_failed() == []  # inside the interval: skipped
        assert manager._servers["flaky"].last_attempt == first_attempt
        assert await manager.retry_failed(force=True) != []  # force does try
    finally:
        await manager.close()


async def test_tool_execution_maps_content_and_errors():
    manager = McpManager({"echo": stdio("echo", ECHO)})
    await manager.start()
    try:
        by_name = {tool.name: tool for tool in manager.tool_defs()}
        echo = by_name["mcp__echo__echo"]
        result = await echo.execute("c1", {"text": "hi"}, None)
        assert result.is_error is False
        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == "echo:hi"

        failing = by_name["mcp__echo__fail"]
        failed = await failing.execute("c2", {}, None)
        assert failed.is_error is True
        assert failed.content[0].text == "nope"
    finally:
        await manager.close()


async def test_calling_a_disconnected_server_is_an_error():
    manager = McpManager({"echo": stdio("echo", ECHO)})
    from zenagent.mcp.client import McpError

    with pytest.raises(McpError, match="not connected"):
        await manager.call("echo", "echo", {"text": "x"})
    with pytest.raises(McpError, match="unknown MCP server"):
        await manager.call("ghost", "echo", {})


async def test_tool_names_are_prefixed_and_sanitized():
    assert tool_name_for("files", "read_file") == "mcp__files__read_file"
    assert tool_name_for("my server", "read file") == "mcp__my_server__read_file"


async def test_refresh_tools_reports_changes_only_when_set_differs():
    manager = McpManager({"echo": stdio("echo", ECHO)})
    await manager.start()
    try:
        assert await manager.refresh_tools() is False  # same tools
        manager._servers["echo"].tools = []  # simulate a changed listing
        assert await manager.refresh_tools() is True
    finally:
        await manager.close()


async def test_close_is_idempotent_and_reaps_processes():
    manager = McpManager({"echo": stdio("echo", ECHO)})
    await manager.start()
    transport = manager._servers["echo"].client.transport
    await manager.close()
    await manager.close()
    assert transport._process is not None and transport._process.returncode is not None


async def test_image_content_from_mcp_is_preserved():
    manager = McpManager({"echo": stdio("echo", ECHO)})
    await manager.start()
    try:
        from zenagent.mcp.client import McpCallResult

        result = manager._make_tool("echo", type("T", (), {
            "name": "img", "description": "", "input_schema": {}
        })())
        mapped = __import__("zenagent.mcp.manager", fromlist=["_to_result"])._to_result(
            McpCallResult(content=[{"type": "image", "data": "AAA", "mimeType": "image/png"}])
        )
        assert isinstance(mapped.content[0], ImageContent)
        assert mapped.content[0].mime_type == "image/png"
        assert result.name == "mcp__echo__img"
    finally:
        await manager.close()
