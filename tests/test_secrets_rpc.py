"""The ``secrets`` RPC kind: list/set/clear/test for model and search keys."""

import json

from zenagent import settings


def build_server(runtime, writes: list[str]):
    from zenagent.cli.rpc import RpcServer

    return RpcServer(runtime=runtime, write_line=writes.append)


async def call(server, writes: list[str], payload: dict) -> dict:
    payload.setdefault("id", "s1")
    assert await server._handle_line(json.dumps(payload))
    return json.loads(writes[-1])


async def test_list_reports_both_catalogs_and_never_key_material(runtime_factory):
    settings.set_api_key("DEEPSEEK_API_KEY", "sk-very-secret")
    runtime = await runtime_factory()
    writes: list[str] = []
    server = build_server(runtime, writes)

    result = await call(server, writes, {"type": "secrets", "op": "list"})

    assert result["ok"] is True
    model_rows = {row["env"]: row for row in result["model_providers"]}
    assert model_rows["DEEPSEEK_API_KEY"]["name"] == "DeepSeek"
    assert model_rows["DEEPSEEK_API_KEY"]["configured"] is True
    assert model_rows["DEEPSEEK_API_KEY"]["source"] == "stored"
    assert model_rows["DEEPSEEK_API_KEY"]["models"] >= 1
    assert "TAVILY_API_KEY" not in model_rows
    search_rows = {row["id"]: row for row in result["search_providers"]}
    assert set(search_rows) == {"deepseek", "tavily", "brave"}
    assert search_rows["deepseek"]["configured"] is True
    assert search_rows["tavily"]["configured"] is False
    assert search_rows["tavily"]["source"] is None
    assert "sk-very-secret" not in json.dumps(result)


async def test_environment_keys_are_reported_as_env_source(runtime_factory):
    runtime = await runtime_factory()
    writes: list[str] = []
    server = build_server(runtime, writes)

    result = await call(server, writes, {"type": "secrets", "op": "list"})

    model_rows = {row["env"]: row for row in result["model_providers"]}
    assert model_rows["DEEPSEEK_API_KEY"]["configured"] is True
    assert model_rows["DEEPSEEK_API_KEY"]["source"] == "env"


async def test_set_persists_the_key_and_pushes_it_into_the_live_agent(
    runtime_factory,
):
    runtime = await runtime_factory()
    runtime.agent.api_key = ""
    writes: list[str] = []
    server = build_server(runtime, writes)

    result = await call(
        server,
        writes,
        {
            "type": "secrets",
            "op": "set",
            "name": "DEEPSEEK_API_KEY",
            "value": "sk-from-ui",
        },
    )

    assert result["ok"] is True
    assert "sk-from-ui" not in json.dumps(result)
    assert settings.get_api_key("DEEPSEEK_API_KEY") == "sk-from-ui"
    assert runtime.agent.api_key == "sk-from-ui"


async def test_clear_removes_the_stored_key_and_restores_the_env_fallback(
    runtime_factory,
):
    settings.set_api_key("DEEPSEEK_API_KEY", "sk-from-ui")
    runtime = await runtime_factory()
    runtime.agent.api_key = "sk-from-ui"
    writes: list[str] = []
    server = build_server(runtime, writes)

    result = await call(
        server,
        writes,
        {"type": "secrets", "op": "clear", "name": "DEEPSEEK_API_KEY"},
    )

    assert result["ok"] is True
    assert settings.get_api_key("DEEPSEEK_API_KEY") == "test-key"
    assert runtime.agent.api_key == "test-key"


async def test_clear_without_an_env_fallback_also_clears_the_live_key(
    runtime_factory, monkeypatch
):
    runtime = await runtime_factory()
    settings.set_api_key("DEEPSEEK_API_KEY", "sk-from-ui")
    runtime.agent.api_key = "sk-from-ui"
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    writes: list[str] = []
    server = build_server(runtime, writes)

    result = await call(
        server,
        writes,
        {"type": "secrets", "op": "clear", "name": "DEEPSEEK_API_KEY"},
    )

    assert result["ok"] is True
    assert settings.get_api_key("DEEPSEEK_API_KEY") is None
    assert runtime.agent.api_key == ""
    assert runtime.missing_api_key == "DEEPSEEK_API_KEY"


async def test_unknown_env_names_and_empty_values_are_rejected(runtime_factory):
    runtime = await runtime_factory()
    writes: list[str] = []
    server = build_server(runtime, writes)

    unknown = await call(
        server,
        writes,
        {"type": "secrets", "op": "set", "name": "PATH", "value": "x"},
    )
    assert unknown["type"] == "error"

    settings.set_api_key("DEEPSEEK_API_KEY", "sk-keep")
    empty = await call(
        server,
        writes,
        {"type": "secrets", "op": "set", "name": "DEEPSEEK_API_KEY", "value": "  "},
    )
    assert empty["type"] == "error"
    assert settings.get_api_key("DEEPSEEK_API_KEY") == "sk-keep"


async def test_unknown_ops_are_rejected(runtime_factory):
    runtime = await runtime_factory()
    writes: list[str] = []
    server = build_server(runtime, writes)

    result = await call(server, writes, {"type": "secrets", "op": "erase"})

    assert result["type"] == "error"
    assert "erase" in result["message"]


async def test_test_op_dispatches_to_the_right_probe(runtime_factory, monkeypatch):
    from zenagent.app import probes

    calls: list[tuple[str, str]] = []

    async def fake_model(catalog, provider_id, **kwargs):
        calls.append(("model", provider_id))
        return probes.ProbeResult(True, "")

    async def fake_search(provider_id, **kwargs):
        calls.append(("search", provider_id))
        return probes.ProbeResult(False, "HTTP 401")

    monkeypatch.setattr(probes, "probe_model_provider", fake_model)
    monkeypatch.setattr(probes, "probe_search_provider", fake_search)
    runtime = await runtime_factory()
    writes: list[str] = []
    server = build_server(runtime, writes)

    model = await call(
        server,
        writes,
        {"type": "secrets", "op": "test", "scope": "model", "target": "deepseek"},
    )
    assert model == {
        "type": "result",
        "id": "s1",
        "ok": True,
        "connected": True,
        "message": "",
    }

    search = await call(
        server,
        writes,
        {"type": "secrets", "op": "test", "scope": "search", "target": "brave"},
    )
    assert search["connected"] is False
    assert search["message"] == "HTTP 401"
    assert calls == [("model", "deepseek"), ("search", "brave")]


async def test_test_op_rejects_unknown_targets(runtime_factory):
    runtime = await runtime_factory()
    writes: list[str] = []
    server = build_server(runtime, writes)

    bad_scope = await call(
        server, writes, {"type": "secrets", "op": "test", "scope": "nope", "target": "x"}
    )
    assert bad_scope["type"] == "error"

    bad_model = await call(
        server,
        writes,
        {"type": "secrets", "op": "test", "scope": "model", "target": "ghost"},
    )
    assert bad_model["type"] == "error"

    bad_search = await call(
        server,
        writes,
        {"type": "secrets", "op": "test", "scope": "search", "target": "ghost"},
    )
    assert bad_search["type"] == "error"
