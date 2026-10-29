"""MCP client: stdio subprocess handshake/tools, HTTP transport, config parsing."""

import json
import sys
from pathlib import Path

import httpx
import pytest

from pyagent.mcp.client import (
    HttpTransport,
    McpClient,
    McpError,
    McpTimeout,
    StdioTransport,
)
from pyagent.mcp.config import (
    McpServerConfig,
    load_mcp_config,
    make_transport,
)

SERVER = Path(__file__).resolve().parent / "fixtures" / "mcp_echo_server.py"


def stdio_transport() -> StdioTransport:
    return StdioTransport([sys.executable, str(SERVER)])


async def test_stdio_handshake_and_tool_listing():
    client = McpClient("echo", stdio_transport())
    await client.start()
    try:
        assert client.server_info["name"] == "echo"
        assert client.protocol_version == "2024-11-05"
        tools = await client.list_tools()
        names = [tool.name for tool in tools]
        assert names == ["echo", "fail", "sleep", "crash"]
        echo = tools[0]
        assert echo.description == "Echo the given text"
        assert echo.input_schema["required"] == ["text"]
    finally:
        await client.close()


async def test_stdio_call_tool_success_and_error_flag():
    client = McpClient("echo", stdio_transport())
    await client.start()
    try:
        ok = await client.call_tool("echo", {"text": "hi"})
        assert ok.is_error is False
        assert ok.content == [{"type": "text", "text": "echo:hi"}]

        failed = await client.call_tool("fail", {})
        assert failed.is_error is True
        assert failed.content[0]["text"] == "nope"
    finally:
        await client.close()


async def test_unknown_method_surfaces_a_rpc_error():
    client = McpClient("echo", stdio_transport())
    await client.start()
    try:
        with pytest.raises(McpError, match="-32601"):
            await client.transport.request("does/not/exist", {}, 5)
    finally:
        await client.close()


async def test_timeout_is_mcp_timeout():
    client = McpClient("echo", stdio_transport(), timeout_s=5)
    await client.start()
    try:
        with pytest.raises(McpTimeout, match="timed out"):
            await client.call_tool("sleep", {}, timeout_s=0.2)
    finally:
        await client.close()


async def test_server_crash_becomes_an_mcp_error():
    client = McpClient("echo", stdio_transport())
    await client.start()
    try:
        with pytest.raises(McpError, match="exited"):
            await client.call_tool("crash", {})
    finally:
        await client.close()


async def test_close_is_idempotent_and_rejects_later_requests():
    transport = stdio_transport()
    client = McpClient("echo", transport)
    await client.start()
    await client.close()
    assert transport._process is not None and transport._process.returncode is not None
    await client.close()  # no error
    with pytest.raises(McpError, match="closed"):
        await client.list_tools()


# ---------------------------------------------------------------------------
# HTTP transport
# ---------------------------------------------------------------------------


def http_handler(requests: list[dict]):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append({"body": body, "headers": dict(request.headers)})
        method = body.get("method")
        if method == "initialize":
            return httpx.Response(
                200,
                headers={"mcp-session-id": "sess-42"},
                json={
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "serverInfo": {"name": "http-echo"},
                        "capabilities": {},
                    },
                },
            )
        if method == "tools/list":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {"tools": [{"name": "ping", "description": "p", "inputSchema": {}}]},
                },
            )
        if method == "tools/call":
            # SSE answer for this one, to cover the event-stream branch
            event = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {"content": [{"type": "text", "text": "pong"}]},
                }
            )
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                text=f"event: message\ndata: {event}\n\n",
            )
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": body.get("id"), "error": {"code": -32601, "message": "no"}},
        )

    return handler


async def test_http_transport_handles_json_and_sse_and_session_header():
    requests: list[dict] = []
    client = httpx.AsyncClient(transport=httpx.MockTransport(http_handler(requests)))
    transport = HttpTransport("https://mcp.test/rpc", client=client)
    mcp = McpClient("remote", transport)
    try:
        await mcp.start()
        assert mcp.server_info["name"] == "http-echo"
        tools = await mcp.list_tools()
        assert [tool.name for tool in tools] == ["ping"]
        called = await mcp.call_tool("echo", {"ignored": True})
        assert called.content[0]["text"] == "pong"
    finally:
        await mcp.close()
        await client.aclose()

    # session id from initialize is echoed on later requests
    assert requests[0]["headers"].get("mcp-session-id") is None
    assert requests[1]["headers"]["mcp-session-id"] == "sess-42"
    assert requests[2]["headers"]["mcp-protocol-version"] == "2024-11-05"
    assert "text/event-stream" in requests[0]["headers"]["accept"]


async def test_http_transport_reports_rpc_errors():
    requests: list[dict] = []
    client = httpx.AsyncClient(transport=httpx.MockTransport(http_handler(requests)))
    transport = HttpTransport("https://mcp.test/rpc", client=client)
    try:
        with pytest.raises(McpError, match="-32601"):
            await transport.request("bogus/method", {}, 5)
    finally:
        await client.aclose()


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------


def test_missing_config_is_empty(tmp_path):
    assert load_mcp_config(tmp_path / "absent.json") == {}


def test_parses_stdio_and_http_servers(tmp_path):
    path = tmp_path / "mcp.json"
    path.write_text(
        json.dumps(
            {
                "servers": {
                    "files": {"command": ["npx", "server-filesystem", "/tmp"], "env": {"A": "1"}},
                    "remote": {"url": "https://x/mcp", "headers": {"authorization": "Bearer t"}},
                }
            }
        )
    )
    servers = load_mcp_config(path)
    assert servers["files"].transport_kind == "stdio"
    assert servers["files"].command == ["npx", "server-filesystem", "/tmp"]
    assert servers["files"].env == {"A": "1"}
    assert servers["remote"].transport_kind == "http"
    assert servers["remote"].headers["authorization"] == "Bearer t"


@pytest.mark.parametrize(
    "raw",
    [
        {"command": ["x"], "url": "https://x"},  # both
        {},  # neither
        {"command": "not-a-list"},  # wrong type
        {"url": "https://x", "headers": ["a"]},  # headers not a map
    ],
)
def test_invalid_server_entries_are_rejected(tmp_path, raw):
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"servers": {"bad": raw}}))
    with pytest.raises(ValueError, match="mcp server"):
        load_mcp_config(path)


def test_broken_json_is_a_value_error(tmp_path):
    path = tmp_path / "mcp.json"
    path.write_text("{not json")
    with pytest.raises(ValueError, match="cannot read mcp config"):
        load_mcp_config(path)


def test_make_transport_picks_the_right_kind():
    assert isinstance(
        make_transport(McpServerConfig(name="a", command=["echo"])), StdioTransport
    )
    assert isinstance(
        make_transport(McpServerConfig(name="b", url="https://x/mcp")), HttpTransport
    )
