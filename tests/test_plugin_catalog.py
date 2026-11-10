"""Builtin catalog: the three official connectors parse and install."""

from __future__ import annotations

import re

import pytest

from zenagent.plugins.catalog import builtin_catalog, catalog_dir, install_builtin
from zenagent.plugins.manifest import parse_manifest

_PLACEHOLDER = re.compile(r"\$\{([A-Z0-9_]+)\}")


def placeholders_of(servers):
    found: set[str] = set()

    def walk(value):
        if isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, str):
            found.update(_PLACEHOLDER.findall(value))

    walk(servers)
    return found


def test_builtin_catalog_lists_three_connectors():
    entries = builtin_catalog()
    assert [entry.id for entry in entries] == ["zen.dingtalk", "zen.feishu", "zen.wecom"]
    assert all(entry.name and entry.description for entry in entries)


def test_builtin_manifests_parse_and_declare_their_placeholders():
    for entry in builtin_catalog():
        manifest = parse_manifest(catalog_dir(entry.id))
        declared = {spec.key for spec in manifest.credentials}
        assert placeholders_of(manifest.mcp_servers) <= declared


def test_secret_connectors_mark_their_url_credentials_secret():
    for plugin_id in ("zen.dingtalk", "zen.wecom"):
        manifest = parse_manifest(catalog_dir(plugin_id))
        assert [spec.secret for spec in manifest.credentials] == [True]
        assert manifest.credentials[0].key == "MCP_URL"


def test_feishu_uses_tools_flag_not_port_flag():
    manifest = parse_manifest(catalog_dir("zen.feishu"))
    command = manifest.mcp_servers["feishu"]["command"]
    assert "-t" in command and "-p" not in command
    assert manifest.mcp_servers["feishu"]["env"]["APP_SECRET"] == "${APP_SECRET}"
    assert manifest.requires_runtime == "Node.js 18+（npx）"


def test_builtin_connectors_ship_a_setup_guide_and_skill():
    for entry in builtin_catalog():
        root = catalog_dir(entry.id)
        manifest = parse_manifest(root)
        assert manifest.setup_guide == "setup.md"
        assert (root / "setup.md").is_file()
        skill = root / manifest.skills / f"{entry.id.split('.')[-1]}-guide" / "SKILL.md"
        assert skill.is_file()


def test_install_builtin_copies_tree(tmp_path):
    manifest = install_builtin("zen.wecom", root=tmp_path / "installed")
    assert manifest.id == "zen.wecom"
    installed = tmp_path / "installed" / "zen.wecom"
    assert (installed / "manifest.json").is_file()
    assert (installed / "skills" / "wecom-guide" / "SKILL.md").is_file()


def test_unknown_builtin_id_rejected(tmp_path):
    with pytest.raises(ValueError):
        install_builtin("zen.nope", root=tmp_path / "installed")
