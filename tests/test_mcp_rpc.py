"""The ``mcp`` RPC kind: layered list/add/remove plus a temporary connection probe."""

import json
import sys
from pathlib import Path

RPC_PATH = Path(__file__).resolve().parents[1] / "zenagent" / "cli" / "rpc.py"
ECHO_SERVER = Path(__file__).resolve().parent / "fixtures" / "mcp_echo_server.py"


def build_server(runtime, writes: list[str]):
    from zenagent.cli.rpc import RpcServer

    return RpcServer(runtime=runtime, write_line=writes.append)


async def call(server, writes: list[str], payload: dict) -> dict:
    payload.setdefault("id", "s1")
    assert await server._handle_line(json.dumps(payload))
    return json.loads(writes[-1])


def isolate_layers(monkeypatch, tmp_path: Path) -> tuple[Path, Path]:
    global_path = tmp_path / "global" / "mcp.json"
    project_path = tmp_path / "project" / ".zenagent" / "mcp.json"
    monkeypatch.setattr("zenagent.mcp.config.config_path", lambda: global_path)
    monkeypatch.setattr(
        "zenagent.project.project_mcp_path", lambda cwd=None: project_path
    )
    return global_path, project_path


def write_layer(path: Path, servers: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"servers": servers}), encoding="utf-8")


async def test_list_merges_layers_and_never_returns_credentials(
    runtime_factory, monkeypatch, tmp_path
):
    global_path, project_path = isolate_layers(monkeypatch, tmp_path)
    write_layer(
        global_path,
        {
            "alpha": {"command": ["alpha"], "env": {"TOKEN": "super-secret"}},
            "shared": {"command": ["global-shared"]},
            "remote": {
                "url": "https://example.test/mcp",
                "headers": {"authorization": "Bearer super-secret"},
            },
        },
    )
    write_layer(project_path, {"shared": {"command": ["project-shared"]}})
    runtime = await runtime_factory()
    writes: list[str] = []
    server = build_server(runtime, writes)

    result = await call(server, writes, {"type": "mcp", "op": "list"})

    assert result["ok"] is True
    assert result["problem"] is None
    rows = {row["name"]: row for row in result["servers"]}
    assert set(rows) == {"alpha", "shared", "remote"}
    assert rows["alpha"]["source"] == "global"
    assert rows["alpha"]["transport"] == "stdio"
    assert rows["alpha"]["command"] == "alpha"
    assert rows["alpha"]["env_keys"] == ["TOKEN"]
    assert rows["shared"]["source"] == "project"
    assert rows["shared"]["overrides_global"] is True
    assert rows["shared"]["command"] == "project-shared"
    assert rows["remote"]["transport"] == "http"
    assert rows["remote"]["url"] == "https://example.test/mcp"
    assert rows["remote"]["header_keys"] == ["authorization"]
    assert rows["alpha"]["state"] == "off"
    assert "super-secret" not in json.dumps(result)


async def test_add_writes_each_layer_with_private_permissions(
    runtime_factory, monkeypatch, tmp_path
):
    global_path, project_path = isolate_layers(monkeypatch, tmp_path)
    runtime = await runtime_factory()
    writes: list[str] = []
    server = build_server(runtime, writes)

    added = await call(
        server,
        writes,
        {
            "type": "mcp",
            "op": "add",
            "name": "files",
            "transport": "stdio",
            "command": "npx -y server-files /tmp",
            "env": {"ROOT": "/tmp"},
        },
    )
    assert added["ok"] is True
    stored = json.loads(global_path.read_text())
    assert stored["servers"]["files"]["command"] == ["npx", "-y", "server-files", "/tmp"]
    assert stored["servers"]["files"]["env"] == {"ROOT": "/tmp"}
    assert global_path.stat().st_mode & 0o777 == 0o600

    remote = await call(
        server,
        writes,
        {
            "type": "mcp",
            "op": "add",
            "name": "remote",
            "transport": "http",
            "url": "https://example.test/mcp",
            "headers": {"authorization": "Bearer secret"},
            "layer": "project",
        },
    )
    assert remote["ok"] is True
    stored = json.loads(project_path.read_text())
    assert stored["servers"]["remote"]["url"] == "https://example.test/mcp"
    assert stored["servers"]["remote"]["headers"] == {"authorization": "Bearer secret"}
    rows = {row["name"]: row for row in remote["servers"]}
    assert rows["remote"]["source"] == "project"
    assert rows["remote"]["header_keys"] == ["authorization"]
    assert "Bearer secret" not in json.dumps(remote)


async def test_add_rejects_incomplete_or_ambiguous_definitions(
    runtime_factory, monkeypatch, tmp_path
):
    global_path, _ = isolate_layers(monkeypatch, tmp_path)
    runtime = await runtime_factory()
    writes: list[str] = []
    server = build_server(runtime, writes)

    missing = await call(
        server, writes, {"type": "mcp", "op": "add", "name": "x", "transport": "stdio"}
    )
    assert missing["type"] == "error"

    both = await call(
        server,
        writes,
        {
            "type": "mcp",
            "op": "add",
            "name": "x",
            "transport": "stdio",
            "command": "x",
            "url": "https://example.test",
        },
    )
    assert both["type"] == "error"

    bad_transport = await call(
        server,
        writes,
        {"type": "mcp", "op": "add", "name": "x", "transport": "carrier-pigeon"},
    )
    assert bad_transport["type"] == "error"
    assert not global_path.exists()


async def test_remove_drops_one_layer_and_reveals_the_global_definition(
    runtime_factory, monkeypatch, tmp_path
):
    global_path, project_path = isolate_layers(monkeypatch, tmp_path)
    write_layer(global_path, {"shared": {"command": ["global-shared"]}})
    write_layer(project_path, {"shared": {"command": ["project-shared"]}})
    runtime = await runtime_factory()
    writes: list[str] = []
    server = build_server(runtime, writes)

    removed = await call(
        server,
        writes,
        {"type": "mcp", "op": "remove", "name": "shared", "layer": "project"},
    )

    assert removed["ok"] is True
    rows = {row["name"]: row for row in removed["servers"]}
    assert rows["shared"]["source"] == "global"
    assert rows["shared"]["command"] == "global-shared"
    assert "shared" not in json.loads(project_path.read_text())["servers"]

    missing = await call(
        server, writes, {"type": "mcp", "op": "remove", "name": "ghost", "layer": "global"}
    )
    assert missing["type"] == "error"


async def test_test_op_dispatches_to_the_probe(runtime_factory, monkeypatch, tmp_path):
    global_path, _ = isolate_layers(monkeypatch, tmp_path)
    write_layer(global_path, {"files": {"command": [sys.executable, str(ECHO_SERVER)]}})
    runtime = await runtime_factory()
    writes: list[str] = []
    server = build_server(runtime, writes)

    result = await call(
        server, writes, {"type": "mcp", "op": "test", "name": "files"}
    )

    assert result["ok"] is True
    assert result["connected"] is True
    assert "echo" in result["tools"]
    assert result["message"] == ""


async def test_test_op_reports_a_missing_command_inline(
    runtime_factory, monkeypatch, tmp_path
):
    global_path, _ = isolate_layers(monkeypatch, tmp_path)
    write_layer(global_path, {"broken": {"command": ["zenagent-no-such-command-xyz"]}})
    runtime = await runtime_factory()
    writes: list[str] = []
    server = build_server(runtime, writes)

    result = await call(
        server, writes, {"type": "mcp", "op": "test", "name": "broken"}
    )

    assert result["ok"] is True
    assert result["connected"] is False
    assert result["message"]


async def test_unknown_servers_and_ops_are_rejected(runtime_factory, monkeypatch, tmp_path):
    isolate_layers(monkeypatch, tmp_path)
    runtime = await runtime_factory()
    writes: list[str] = []
    server = build_server(runtime, writes)

    unknown = await call(
        server, writes, {"type": "mcp", "op": "test", "name": "ghost"}
    )
    assert unknown["type"] == "error"

    bad_op = await call(server, writes, {"type": "mcp", "op": "edit"})
    assert bad_op["type"] == "error"
    assert "edit" in bad_op["message"]
