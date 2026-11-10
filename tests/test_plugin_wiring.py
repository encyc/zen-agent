"""Assembly: plugin contributions merged with the user's own MCP servers."""

from __future__ import annotations

import json

from zenagent import settings
from zenagent.app.plugin_wiring import assemble_plugins
from zenagent.mcp.config import McpServerConfig
from zenagent.plugins import discovery
from zenagent.plugins.state import set_credentials, set_enabled


def make_plugin(
    plugin_id="zen.demo",
    *,
    server=None,
    credentials=None,
    skills=True,
):
    root = discovery.installed_root() / plugin_id
    root.mkdir(parents=True)
    data = {
        "schemaVersion": 1,
        "id": plugin_id,
        "name": "Demo",
        "version": "0.1.0",
        "description": "demo plugin",
        "contributes": {"mcpServers": server or {}, "skills": "skills"},
        "credentials": credentials or [],
        "setup": {},
    }
    (root / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
    if skills:
        guide = root / "skills" / "demo-guide"
        guide.mkdir(parents=True)
        (guide / "SKILL.md").write_text("---\nname: demo-guide\n---\nbody", encoding="utf-8")
    return root


URL_CREDENTIAL = [{"key": "MCP_URL", "label": "MCP URL", "secret": True}]


def test_user_servers_win_over_plugin_servers():
    make_plugin(server={"demo": {"url": "${MCP_URL}"}}, credentials=URL_CREDENTIAL)
    set_credentials("zen.demo", {"MCP_URL": "https://plugin.example/mcp"})
    user = {
        "zen.demo.demo": McpServerConfig(
            name="zen.demo.demo", url="https://user.example/mcp"
        )
    }
    assembly = assemble_plugins(user_configs=user)
    assert assembly.statuses == {"zen.demo": "ready"}
    assert assembly.configs["zen.demo.demo"].url == "https://user.example/mcp"


def test_disabled_plugin_is_excluded():
    make_plugin(server={"demo": {"url": "${MCP_URL}"}}, credentials=URL_CREDENTIAL)
    set_credentials("zen.demo", {"MCP_URL": "https://plugin.example/mcp"})
    set_enabled("zen.demo", False)
    assembly = assemble_plugins()
    assert assembly.statuses == {"zen.demo": "disabled"}
    assert assembly.configs == {}
    assert assembly.skill_roots == []


def test_missing_credentials_sets_status_without_servers():
    make_plugin(
        server={"demo": {"url": "${MCP_URL}"}},
        credentials=URL_CREDENTIAL,
    )
    assembly = assemble_plugins()
    assert assembly.statuses == {"zen.demo": "needs_credentials"}
    assert assembly.configs == {}
    assert any(
        "zen.demo: missing credentials MCP_URL" in warning
        for warning in assembly.warnings
    )
    # The guide skill does not depend on credentials, so it still ships.
    assert assembly.skill_roots == [discovery.installed_root() / "zen.demo" / "skills"]


def test_bad_plugin_server_shape_warns_and_skips():
    make_plugin(server={"demo": {"command": ["echo"], "url": "https://x"}})
    assembly = assemble_plugins()
    assert assembly.statuses == {"zen.demo": "ready"}
    assert assembly.configs == {}
    assert any("zen.demo.demo" in warning for warning in assembly.warnings)
    assert any("both" in warning for warning in assembly.warnings)


def test_non_string_credential_degrades_to_needs_credentials():
    make_plugin(server={"demo": {"url": "${MCP_URL}"}}, credentials=URL_CREDENTIAL)
    store = settings.load()
    store.api_keys["plugin:zen.demo:MCP_URL"] = 42  # hand-edited junk
    settings.save(store)
    assembly = assemble_plugins()
    assert assembly.statuses == {"zen.demo": "needs_credentials"}
    assert assembly.configs == {}
