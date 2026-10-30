"""MCP client: stdio subprocess handshake/tools, HTTP transport, config parsing."""

import json
import sys
from pathlib import Path

import httpx
import pytest

from zenagent.mcp.client import (
    HttpTransport,
    McpClient,
    McpError,
    McpTimeout,
    StdioTransport,
)
from zenagent.mcp.config import (
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


# ---------------------------------------------------------------------------
# project-layer config stacking (global ~/.zenagent/mcp.json -> <repo>/.zenagent/mcp.json)
# ---------------------------------------------------------------------------


def stacked_mcp_config(
    tmp_path, monkeypatch, *, global_servers=None, project_servers=None
):
    """Isolated global mcp.json plus a git repo carrying a project mcp.json."""
    home = tmp_path / "home"
    home.mkdir()
    global_path = home / "mcp.json"
    if global_servers is not None:
        global_path.write_text(
            json.dumps({"servers": global_servers}), encoding="utf-8"
        )
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    project_path = repo / ".zenagent" / "mcp.json"
    if project_servers is not None:
        project_path.parent.mkdir(parents=True)
        project_path.write_text(
            json.dumps({"servers": project_servers}), encoding="utf-8"
        )
    monkeypatch.setattr("zenagent.mcp.config.config_path", lambda: global_path)
    return repo, project_path


def test_project_mcp_servers_override_same_name_and_keep_the_rest(tmp_path, monkeypatch):
    repo, _ = stacked_mcp_config(
        tmp_path,
        monkeypatch,
        global_servers={
            "shared": {"url": "https://global.example/mcp"},
            "global-only": {"command": ["global-server"]},
        },
        project_servers={
            "shared": {"url": "https://project.example/mcp"},
            "project-only": {"command": ["project-server"]},
        },
    )
    monkeypatch.chdir(repo)

    servers = load_mcp_config()

    assert set(servers) == {"shared", "global-only", "project-only"}
    assert servers["shared"].url == "https://project.example/mcp"
    assert servers["global-only"].command == ["global-server"]
    assert servers["project-only"].command == ["project-server"]


def test_project_mcp_config_works_without_the_global_file(tmp_path, monkeypatch):
    repo, _ = stacked_mcp_config(
        tmp_path,
        monkeypatch,
        project_servers={"files": {"command": ["npx", "server-filesystem", "/tmp"]}},
    )
    monkeypatch.chdir(repo)

    servers = load_mcp_config()

    assert list(servers) == ["files"]
    assert servers["files"].command == ["npx", "server-filesystem", "/tmp"]


def test_missing_mcp_configs_mean_no_servers(tmp_path, monkeypatch):
    repo, _ = stacked_mcp_config(tmp_path, monkeypatch)
    monkeypatch.chdir(repo)

    assert load_mcp_config() == {}


def test_explicit_mcp_path_ignores_the_project_layer(tmp_path, monkeypatch):
    repo, _ = stacked_mcp_config(
        tmp_path,
        monkeypatch,
        global_servers={"global": {"url": "https://global.example/mcp"}},
        project_servers={"project": {"url": "https://project.example/mcp"}},
    )
    monkeypatch.chdir(repo)
    explicit = tmp_path / "explicit.json"
    explicit.write_text(
        json.dumps({"servers": {"explicit": {"url": "https://explicit.example/mcp"}}})
    )

    servers = load_mcp_config(explicit)

    assert set(servers) == {"explicit"}


def test_project_mcp_layer_follows_an_explicit_cwd(tmp_path, monkeypatch):
    repo, _ = stacked_mcp_config(
        tmp_path,
        monkeypatch,
        project_servers={"files": {"command": ["server-filesystem"]}},
    )
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    assert load_mcp_config() == {}
    assert list(load_mcp_config(cwd=repo)) == ["files"]


def test_broken_project_mcp_config_raises_with_its_path(tmp_path, monkeypatch):
    repo, project_path = stacked_mcp_config(tmp_path, monkeypatch)
    project_path.parent.mkdir(parents=True, exist_ok=True)
    project_path.write_text("{not json", encoding="utf-8")
    monkeypatch.chdir(repo)

    with pytest.raises(ValueError) as excinfo:
        load_mcp_config()
    assert "cannot read mcp config" in str(excinfo.value)
    assert str(project_path) in str(excinfo.value)
