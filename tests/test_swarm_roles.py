import json
from pathlib import Path

import pytest

from zenagent.app.swarm.roles import (
    agents_root,
    discover_roles,
    parse_role,
    workspaces_root,
)
from zenagent.app.swarm.roster import role_view, write_role


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    return home


def _write_role(role_id: str, manifest: dict, prompt: str = "You are a role.") -> Path:
    role_dir = agents_root() / role_id
    role_dir.mkdir(parents=True)
    (role_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (role_dir / "prompt.md").write_text(prompt, encoding="utf-8")
    return role_dir


def _manifest(role_id: str = "research", **overrides) -> dict:
    return {"schemaVersion": 1, "id": role_id, "name": role_id.title(), **overrides}


def test_parse_role_reads_fields_and_prompt(home):
    role_dir = _write_role("research", _manifest(model="deepseek-chat"))
    role, warnings = parse_role(role_dir)

    assert role is not None
    assert role.id == "research"
    assert role.name == "Research"
    assert role.model == "deepseek-chat"
    assert role.system_prompt == "You are a role."
    assert role.mode == "ask"
    assert role.inherit_project is False
    assert role.tools is None
    assert role.root == (Path.home() / ".zenagent-workspaces" / "research").resolve()
    assert warnings == []


def test_bad_manifest_is_skipped_with_warning(home):
    bad = agents_root() / "broken"
    bad.mkdir(parents=True)
    (bad / "manifest.json").write_text("{not json", encoding="utf-8")
    _write_role("research", _manifest())

    role, warnings = parse_role(bad)
    assert role is None
    assert warnings and "broken" in warnings[0]

    roles, discover_warnings = discover_roles()
    assert [entry.id for entry in roles] == ["research"]
    assert discover_warnings


def test_missing_credentials_drops_only_that_server(home, monkeypatch):
    monkeypatch.delenv("SWARM_TEST_APP_ID", raising=False)
    role_dir = _write_role(
        "research",
        _manifest(
            mcpServers={
                "feishu": {
                    "command": ["npx", "lark"],
                    "env": {"APP_ID": "${SWARM_TEST_APP_ID}"},
                },
                "local": {"command": ["echo"]},
            }
        ),
    )

    role, warnings = parse_role(role_dir)

    assert role is not None
    assert "local" in role.mcp_servers
    assert "feishu" not in role.mcp_servers
    assert role.missing_credentials == ["SWARM_TEST_APP_ID"]
    assert any("feishu" in warning for warning in warnings)


def test_workspace_defaults_outside_protected_home(home):
    role_dir = _write_role("research", _manifest())
    role, _ = parse_role(role_dir)

    assert role is not None
    assert role.root == (workspaces_root() / "research").resolve()
    assert str(role.root).startswith(str(Path.home() / ".zenagent-workspaces"))
    assert role.root.is_dir()


def test_write_role_keeps_fields_the_client_did_not_send(home):
    role_dir = _write_role(
        "research",
        _manifest(
            model="deepseek-chat",
            skills=["skills"],
            plugins=["zen.feishu"],
            inheritProject=True,
        ),
    )

    write_role(
        "research",
        {"schemaVersion": 1, "name": "Renamed", "mode": "ask", "systemPrompt": "v2"},
    )

    raw = json.loads((role_dir / "manifest.json").read_text(encoding="utf-8"))
    assert raw["name"] == "Renamed"
    assert raw["model"] == "deepseek-chat"
    assert raw["skills"] == ["skills"]
    assert raw["plugins"] == ["zen.feishu"]
    assert raw["inheritProject"] is True
    assert "systemPrompt" not in raw
    assert (role_dir / "prompt.md").read_text(encoding="utf-8") == "v2"


def test_write_role_null_deletes_a_field(home):
    role_dir = _write_role("research", _manifest(model="deepseek-chat", tools=["bash"]))

    write_role("research", {"schemaVersion": 1, "name": "Research", "model": None, "tools": None})

    raw = json.loads((role_dir / "manifest.json").read_text(encoding="utf-8"))
    assert "model" not in raw
    assert "tools" not in raw


def test_role_view_carries_the_persona_for_editing(home):
    role_dir = _write_role("research", _manifest())
    role, _ = parse_role(role_dir)

    assert role is not None
    assert role_view(role)["systemPrompt"] == "You are a role."


def test_role_config_beats_plugin_contribution(home, tmp_path):
    plugin_dir = tmp_path / "plugins" / "zen.feishu"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "manifest.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "id": "zen.feishu",
                "name": "Feishu",
                "version": "1.0.0",
                "description": "Feishu connector",
                "contributes": {
                    "mcpServers": {"lark": {"command": ["plugin-lark"]}}
                },
            }
        ),
        encoding="utf-8",
    )
    role_dir = _write_role(
        "research",
        _manifest(
            plugins=["zen.feishu"],
            mcpServers={"zen.feishu.lark": {"command": ["role-lark"]}},
        ),
    )

    role, _ = parse_role(role_dir)

    assert role is not None
    assert role.mcp_servers["zen.feishu.lark"] == {"command": ["role-lark"]}
