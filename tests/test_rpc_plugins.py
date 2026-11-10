"""The ``plugins`` RPC kind: catalog, install, enable, configure, uninstall."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

from zenagent import settings


def build_server(runtime, writes: list[str]):
    from zenagent.cli.rpc import RpcServer

    return RpcServer(runtime=runtime, write_line=writes.append)


async def call(server, writes: list[str], payload: dict) -> dict:
    payload.setdefault("id", "p1")
    assert await server._handle_line(json.dumps(payload))
    return json.loads(writes[-1])


def isolate_apply(runtime, monkeypatch, *, side_effect=None) -> AsyncMock:
    mock = AsyncMock(side_effect=side_effect)
    monkeypatch.setattr(runtime, "reconfigure_plugins", mock)
    return mock


async def test_plugins_list_exposes_catalog_without_secret_values(
    runtime_factory, monkeypatch
):
    runtime = await runtime_factory()
    isolate_apply(runtime, monkeypatch)
    writes: list[str] = []
    server = build_server(runtime, writes)

    result = await call(server, writes, {"type": "plugins", "op": "list"})

    assert result["ok"] is True
    assert result["problem"] is None
    assert result["apply_pending"] is False
    assert result["plugins"] == []
    assert [row["id"] for row in result["catalog"]] == [
        "zen.dingtalk",
        "zen.feishu",
        "zen.wecom",
    ]
    assert all(row["installed"] is False for row in result["catalog"])


async def test_install_enable_configure_roundtrip(runtime_factory, monkeypatch):
    runtime = await runtime_factory()
    apply = isolate_apply(runtime, monkeypatch)
    writes: list[str] = []
    server = build_server(runtime, writes)

    installed = await call(
        server, writes, {"type": "plugins", "op": "install", "plugin": "zen.feishu"}
    )
    row = next(item for item in installed["plugins"] if item["id"] == "zen.feishu")
    assert row["status"] == "needs_credentials"
    assert [spec["key"] for spec in row["credentials"]] == ["APP_ID", "APP_SECRET"]
    assert all(spec["set"] is False for spec in row["credentials"])
    assert installed["catalog"][1]["installed"] is True
    apply.assert_awaited()

    configured = await call(
        server,
        writes,
        {
            "type": "plugins",
            "op": "configure",
            "plugin": "zen.feishu",
            "values": {"APP_ID": "cli_1", "APP_SECRET": "topsecret"},
        },
    )
    assert configured["ok"] is True
    assert "topsecret" not in json.dumps(configured)
    row = next(item for item in configured["plugins"] if item["id"] == "zen.feishu")
    assert row["status"] == "ready"
    assert all(spec["set"] is True for spec in row["credentials"])
    stored = settings.load().api_keys
    assert stored["plugin:zen.feishu:APP_ID"] == "cli_1"


async def test_apply_defers_while_streaming(runtime_factory, monkeypatch):
    runtime = await runtime_factory()
    isolate_apply(runtime, monkeypatch, side_effect=RuntimeError("busy"))
    writes: list[str] = []
    server = build_server(runtime, writes)
    await call(server, writes, {"type": "plugins", "op": "install", "plugin": "zen.wecom"})

    result = await call(
        server, writes, {"type": "plugins", "op": "configure", "plugin": "zen.wecom", "values": {"MCP_URL": "https://x"}}
    )

    assert result["apply_pending"] is True


async def test_disable_then_enable_roundtrip(runtime_factory, monkeypatch):
    runtime = await runtime_factory()
    isolate_apply(runtime, monkeypatch)
    writes: list[str] = []
    server = build_server(runtime, writes)
    await call(server, writes, {"type": "plugins", "op": "install", "plugin": "zen.wecom"})

    disabled = await call(
        server, writes, {"type": "plugins", "op": "disable", "plugin": "zen.wecom"}
    )
    row = next(item for item in disabled["plugins"] if item["id"] == "zen.wecom")
    assert row["enabled"] is False
    assert row["status"] == "disabled"

    enabled = await call(
        server, writes, {"type": "plugins", "op": "enable", "plugin": "zen.wecom"}
    )
    row = next(item for item in enabled["plugins"] if item["id"] == "zen.wecom")
    assert row["enabled"] is True
    assert row["status"] == "needs_credentials"


async def test_uninstall_clears_state(runtime_factory, monkeypatch):
    runtime = await runtime_factory()
    isolate_apply(runtime, monkeypatch)
    writes: list[str] = []
    server = build_server(runtime, writes)
    await call(server, writes, {"type": "plugins", "op": "install", "plugin": "zen.wecom"})
    await call(
        server,
        writes,
        {"type": "plugins", "op": "configure", "plugin": "zen.wecom", "values": {"MCP_URL": "https://x"}},
    )

    removed = await call(
        server, writes, {"type": "plugins", "op": "uninstall", "plugin": "zen.wecom"}
    )

    assert removed["plugins"] == []
    assert removed["catalog"][2]["installed"] is False
    assert not any(
        name.startswith("plugin:zen.wecom:") for name in settings.load().api_keys
    )


async def test_install_without_id_or_path_reports_error(runtime_factory, monkeypatch):
    runtime = await runtime_factory()
    isolate_apply(runtime, monkeypatch)
    writes: list[str] = []
    server = build_server(runtime, writes)

    result = await call(server, writes, {"type": "plugins", "op": "install"})

    assert result["type"] == "error"
    assert "id or path" in result["message"]


async def test_unknown_op_reports_error_frame(runtime_factory, monkeypatch):
    runtime = await runtime_factory()
    isolate_apply(runtime, monkeypatch)
    writes: list[str] = []
    server = build_server(runtime, writes)

    result = await call(server, writes, {"type": "plugins", "op": "bogus", "plugin": "x"})

    assert result["type"] == "error"
    assert "bogus" in result["message"]


async def test_dispatch_accepts_plugins_kind(runtime_factory, monkeypatch):
    runtime = await runtime_factory()
    isolate_apply(runtime, monkeypatch)
    writes: list[str] = []
    server = build_server(runtime, writes)

    result = await call(server, writes, {"type": "plugins", "op": "list"})

    assert result["type"] == "result"
    assert "unknown type" not in result.get("message", "")


async def test_deferred_apply_is_tracked_and_stops_after_close(runtime_factory, monkeypatch):
    runtime = await runtime_factory()
    apply = isolate_apply(runtime, monkeypatch, side_effect=RuntimeError("busy"))
    writes: list[str] = []
    server = build_server(runtime, writes)

    assert await server._apply_or_defer() is False
    tasks = server._plugin_tasks
    assert len(tasks) == 1

    server._closed = True
    await next(iter(tasks))
    assert apply.await_count == 1
    assert server._plugin_tasks == set()
