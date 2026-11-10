"""Manifest parsing: strict at install time, person-readable errors."""

from __future__ import annotations

import json

import pytest

from zenagent.plugins.manifest import parse_manifest

VALID = {
    "schemaVersion": 1,
    "id": "zen.feishu",
    "name": "飞书",
    "version": "0.1.0",
    "description": "飞书官方 MCP 连接器",
    "contributes": {
        "mcpServers": {
            "feishu": {
                "command": ["npx", "-y", "@larksuiteoapi/lark-mcp", "-t", "preset.im.default"],
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
    "setup": {"guide": "setup.md", "requiresRuntime": "Node.js 18+（npx）"},
}


def write(root, data):
    (root / "manifest.json").write_text(json.dumps(data), encoding="utf-8")


def test_valid_manifest_parses(tmp_path):
    write(tmp_path, VALID)
    (tmp_path / "skills" / "feishu-guide").mkdir(parents=True)
    manifest = parse_manifest(tmp_path)
    assert manifest.id == "zen.feishu"
    assert manifest.mcp_servers["feishu"]["env"]["APP_SECRET"] == "${APP_SECRET}"
    assert [c.secret for c in manifest.credentials] == [False, True]
    assert manifest.requires_runtime == "Node.js 18+（npx）"
    assert manifest.skills == "skills"


def test_unknown_placeholder_rejected(tmp_path):
    data = json.loads(json.dumps(VALID))
    data["contributes"]["mcpServers"]["feishu"]["url"] = "${MCP_URL}"
    del data["contributes"]["mcpServers"]["feishu"]["command"]
    write(tmp_path, data)
    with pytest.raises(ValueError, match="MCP_URL"):
        parse_manifest(tmp_path)


def test_skills_path_escape_rejected(tmp_path):
    for bad in ("/abs/skills", "../outside"):
        data = json.loads(json.dumps(VALID))
        data["contributes"]["skills"] = bad
        write(tmp_path, data)
        with pytest.raises(ValueError):
            parse_manifest(tmp_path)


def test_bad_id_rejected(tmp_path):
    data = json.loads(json.dumps(VALID))
    data["id"] = "Zen.Feishu"
    write(tmp_path, data)
    with pytest.raises(ValueError):
        parse_manifest(tmp_path)


def test_schema_version_pinned(tmp_path):
    data = json.loads(json.dumps(VALID))
    data["schemaVersion"] = 2
    write(tmp_path, data)
    with pytest.raises(ValueError):
        parse_manifest(tmp_path)


def test_duplicate_credential_keys_rejected(tmp_path):
    data = json.loads(json.dumps(VALID))
    data["credentials"].append({"key": "APP_ID", "label": "again"})
    write(tmp_path, data)
    with pytest.raises(ValueError):
        parse_manifest(tmp_path)


def test_declared_extension_must_exist(tmp_path):
    data = json.loads(json.dumps(VALID))
    data["contributes"]["extension"] = "ext.py"
    write(tmp_path, data)
    with pytest.raises(ValueError):
        parse_manifest(tmp_path)
    (tmp_path / "ext.py").write_text("")
    assert parse_manifest(tmp_path).extension == "ext.py"


def test_missing_manifest_file_rejected(tmp_path):
    with pytest.raises(ValueError):
        parse_manifest(tmp_path)


def test_non_object_manifest_rejected(tmp_path):
    (tmp_path / "manifest.json").write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError):
        parse_manifest(tmp_path)
