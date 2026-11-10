"""Plugin state: enable flags, namespaced credentials, contribution resolution."""

from __future__ import annotations

import json

from zenagent import settings
from zenagent.plugins.manifest import parse_manifest
from zenagent.plugins.state import (
    clear_plugin,
    credential_name,
    credentials_for,
    is_enabled,
    resolve,
    set_credentials,
    set_enabled,
)

VALID = {
    "schemaVersion": 1,
    "id": "zen.feishu",
    "name": "飞书",
    "version": "0.1.0",
    "description": "飞书官方 MCP 连接器",
    "contributes": {
        "mcpServers": {
            "feishu": {
                "command": ["npx", "-y", "@larksuiteoapi/lark-mcp"],
                "env": {"APP_ID": "${APP_ID}", "APP_SECRET": "${APP_SECRET}"},
            }
        },
        "skills": "skills",
        "extension": None,
    },
    "credentials": [
        {"key": "APP_ID", "label": "App ID", "secret": False},
        {"key": "APP_SECRET", "label": "App Secret", "secret": True},
    ],
    "setup": {"guide": "setup.md"},
}


def manifest_for(root):
    (root / "manifest.json").write_text(json.dumps(VALID), encoding="utf-8")
    return parse_manifest(root)


def test_enabled_defaults_true_and_roundtrips():
    assert is_enabled("zen.feishu") is True
    set_enabled("zen.feishu", False)
    assert is_enabled("zen.feishu") is False
    assert settings.load().prefs["plugins"]["zen.feishu"]["enabled"] is False


def test_corrupt_plugin_state_degrades_to_enabled():
    store = settings.load()
    store.prefs["plugins"] = "oops"
    settings.save(store)
    assert is_enabled("x") is True
    set_enabled("x", False)
    assert is_enabled("x") is False


def test_credentials_use_namespaced_keys():
    assert credential_name("zen.feishu", "APP_ID") == "plugin:zen.feishu:APP_ID"
    set_credentials("zen.feishu", {"APP_ID": " cli_1 ", "APP_SECRET": "sec"})
    assert settings.load().api_keys["plugin:zen.feishu:APP_ID"] == "cli_1"
    assert credentials_for("zen.feishu") == {"APP_ID": "cli_1", "APP_SECRET": "sec"}


def test_empty_value_deletes_credential():
    set_credentials("zen.feishu", {"APP_ID": "cli_1"})
    set_credentials("zen.feishu", {"APP_ID": "  "})
    assert "plugin:zen.feishu:APP_ID" not in settings.load().api_keys


def test_credentials_ignore_environment(monkeypatch):
    monkeypatch.setenv("APP_ID", "from-env")
    assert credentials_for("zen.feishu") == {}


def test_clear_plugin_drops_state_and_credentials():
    set_enabled("zen.feishu", False)
    set_credentials("zen.feishu", {"APP_ID": "cli_1", "APP_SECRET": "sec"})
    set_credentials("zen.dingtalk", {"APP_ID": "other"})
    clear_plugin("zen.feishu")
    store = settings.load()
    assert "zen.feishu" not in store.prefs.get("plugins", {})
    assert not any(name.startswith("plugin:zen.feishu:") for name in store.api_keys)
    assert store.api_keys["plugin:zen.dingtalk:APP_ID"] == "other"
    assert is_enabled("zen.feishu") is True


def test_resolve_missing_credentials_suppresses_servers(tmp_path):
    manifest = manifest_for(tmp_path)
    resolved = resolve(manifest, {"APP_ID": "cli_1"})
    assert resolved.missing == ["APP_SECRET"]
    assert resolved.servers == {}


def test_resolve_substitutes_and_names_servers(tmp_path):
    manifest = manifest_for(tmp_path)
    (tmp_path / "skills").mkdir()
    resolved = resolve(manifest, {"APP_ID": "cli_1", "APP_SECRET": "sec"})
    server = resolved.servers["zen.feishu.feishu"]
    assert server["env"]["APP_ID"] == "cli_1"
    assert server["env"]["APP_SECRET"] == "sec"
    assert server["command"] == ["npx", "-y", "@larksuiteoapi/lark-mcp"]
    assert resolved.missing == []
    assert resolved.skill_roots == [tmp_path / "skills"]
    assert resolved.extension is None


def test_resolve_keeps_manifest_untouched(tmp_path):
    manifest = manifest_for(tmp_path)
    resolve(manifest, {"APP_ID": "cli_1", "APP_SECRET": "sec"})
    assert manifest.mcp_servers["feishu"]["env"]["APP_SECRET"] == "${APP_SECRET}"
