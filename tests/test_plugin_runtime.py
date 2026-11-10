"""Runtime integration: plugins load at bootstrap and hot-reconfigure."""

from __future__ import annotations

import json

import pytest

from zenagent.app import plugin_wiring
from zenagent.plugins import discovery
from zenagent.plugins.state import set_credentials, set_enabled


def make_plugin(server=None, credentials=None):
    root = discovery.installed_root() / "zen.demo"
    root.mkdir(parents=True)
    data = {
        "schemaVersion": 1,
        "id": "zen.demo",
        "name": "Demo",
        "version": "0.1.0",
        "description": "demo plugin",
        "contributes": {"mcpServers": server or {}, "skills": "skills"},
        "credentials": credentials or [],
        "setup": {},
    }
    (root / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
    guide = root / "skills" / "demo-guide"
    guide.mkdir(parents=True)
    (guide / "SKILL.md").write_text(
        "---\nname: demo-guide\ndescription: demo\n---\nbody", encoding="utf-8"
    )
    return root


URL_CREDENTIAL = [{"key": "MCP_URL", "label": "MCP URL", "secret": True}]


class FakeManager:
    def __init__(self, configs):
        self.configs = dict(configs)
        self.closed = False

    def tool_defs(self):
        return []

    async def close(self):
        self.closed = True


@pytest.fixture
def fake_mcp(monkeypatch):
    started: list[FakeManager] = []

    async def fake_start(configs, *, retry_interval_s, runtime=None):
        if not configs:
            return None
        manager = FakeManager(configs)
        started.append(manager)
        return manager

    monkeypatch.setattr(plugin_wiring, "start_mcp", fake_start)
    # Never mix the developer's real ~/.zenagent/mcp.json into these cases.
    monkeypatch.setattr(plugin_wiring, "load_mcp_config", lambda *a, **k: {})
    return started


async def test_bootstrap_installs_plugin_skills_and_servers(runtime_factory, fake_mcp):
    make_plugin(
        server={"demo": {"url": "${MCP_URL}"}},
        credentials=URL_CREDENTIAL,
    )
    set_credentials("zen.demo", {"MCP_URL": "https://plugin.example/mcp"})
    runtime = await runtime_factory(mcp=True, skills=True)

    assert runtime.plugin_statuses == {"zen.demo": "ready"}
    assert any(skill.name == "demo-guide" for skill in runtime.skills)
    assert runtime.mcp is fake_mcp[-1]
    assert fake_mcp[-1].configs["zen.demo.demo"].url == "https://plugin.example/mcp"


async def test_bootstrap_without_credentials_skips_servers(runtime_factory, fake_mcp):
    make_plugin(server={"demo": {"url": "${MCP_URL}"}}, credentials=URL_CREDENTIAL)
    runtime = await runtime_factory(mcp=True, skills=True)

    assert runtime.plugin_statuses == {"zen.demo": "needs_credentials"}
    assert runtime.mcp is None
    assert fake_mcp == []
    assert any("missing credentials MCP_URL" in w for w in runtime.warnings)


async def test_reconfigure_swaps_manager_and_closes_old(runtime_factory, fake_mcp):
    make_plugin(server={"demo": {"url": "${MCP_URL}"}}, credentials=URL_CREDENTIAL)
    set_credentials("zen.demo", {"MCP_URL": "https://plugin.example/mcp"})
    runtime = await runtime_factory(mcp=True, skills=True)
    old = runtime.mcp
    assert old is not None

    set_enabled("zen.demo", False)
    notes = await runtime.reconfigure_plugins()

    assert old.closed is True
    assert runtime.mcp is None
    assert runtime.plugin_statuses == {"zen.demo": "disabled"}
    assert notes == ["1 plugins (0 ready)"]
    assert not any(skill.name == "demo-guide" for skill in runtime.skills)


async def test_reconfigure_refuses_while_streaming(runtime_factory, monkeypatch):
    runtime = await runtime_factory()
    monkeypatch.setattr(
        type(runtime.agent), "is_streaming", property(lambda self: True)
    )
    with pytest.raises(RuntimeError, match="reload"):
        await runtime.reconfigure_plugins()
