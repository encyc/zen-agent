import json
from pathlib import Path

import pytest

from zenagent.app.swarm.roles import (
    agents_root,
    discover_roles,
    parse_role,
    project_agents_root,
    project_state_root,
    role_session_path,
    workspaces_root,
)
from zenagent.app.swarm.roster import copy_role, role_signature, role_view, write_role


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    return home


@pytest.fixture
def proj(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    return root


def _write_role(
    role_id: str,
    manifest: dict,
    prompt: str = "You are a role.",
    *,
    root: Path | None = None,
) -> Path:
    role_dir = (agents_root() if root is None else project_agents_root(root)) / role_id
    role_dir.mkdir(parents=True)
    (role_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (role_dir / "prompt.md").write_text(prompt, encoding="utf-8")
    return role_dir


def test_project_state_root_is_under_the_project_dir(home, proj):
    from zenagent import project

    assert project_state_root(proj) == project.project_dir(proj) / "swarm"


def test_discover_roles_reads_the_project_local_agents_dir(home, proj):
    _write_role("research", {"id": "research", "name": "R"}, root=proj)

    roles, warnings = discover_roles(proj)

    assert [role.id for role in roles] == ["research"]
    # project-local default: workspace is the project root, inherit_project on
    assert roles[0].root == proj.resolve()
    assert roles[0].inherit_project is True
    assert warnings == []


def test_library_role_keeps_isolated_defaults(home):
    _write_role("coder", {"id": "coder", "name": "C"})

    roles, _ = discover_roles(None)

    assert [role.id for role in roles] == ["coder"]
    assert roles[0].inherit_project is False
    assert roles[0].root == (workspaces_root() / "coder").resolve()


def test_role_session_path_is_per_project(home, tmp_path):
    r1, r2 = tmp_path / "a", tmp_path / "b"
    r1.mkdir()
    r2.mkdir()

    p1 = role_session_path("research", root=r1)
    p2 = role_session_path("research", root=r2)

    assert p1.parent != p2.parent
    assert p1.is_relative_to(project_state_root(r1) / "agents" / "research")
    assert p1.exists()


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


def test_write_role_keeps_fields_the_client_did_not_send(home, proj):
    role_dir = _write_role(
        "research",
        _manifest(
            model="deepseek-chat",
            skills=["skills"],
            plugins=["zen.feishu"],
            inheritProject=True,
        ),
        root=proj,
    )

    write_role(
        "research",
        {"schemaVersion": 1, "name": "Renamed", "mode": "ask", "systemPrompt": "v2"},
        root=proj,
    )

    raw = json.loads((role_dir / "manifest.json").read_text(encoding="utf-8"))
    assert raw["name"] == "Renamed"
    assert raw["model"] == "deepseek-chat"
    assert raw["skills"] == ["skills"]
    assert raw["plugins"] == ["zen.feishu"]
    assert raw["inheritProject"] is True
    assert "systemPrompt" not in raw
    assert (role_dir / "prompt.md").read_text(encoding="utf-8") == "v2"


def test_write_role_null_deletes_a_field(home, proj):
    role_dir = _write_role(
        "research", _manifest(model="deepseek-chat", tools=["bash"]), root=proj
    )

    write_role(
        "research",
        {"schemaVersion": 1, "name": "Research", "model": None, "tools": None},
        root=proj,
    )

    raw = json.loads((role_dir / "manifest.json").read_text(encoding="utf-8"))
    assert "model" not in raw
    assert "tools" not in raw


def test_role_view_carries_the_persona_for_editing(home):
    role_dir = _write_role("research", _manifest())
    role, _ = parse_role(role_dir)

    assert role is not None
    assert role_view(role)["systemPrompt"] == "You are a role."


def test_role_view_carries_raw_manifest_entries_for_editing(home, monkeypatch):
    monkeypatch.setenv("SWARM_TEST_APP_ID", "secret-app-id")
    role_dir = _write_role(
        "research",
        _manifest(
            skills=["skills", "extra"],
            plugins=["zen.feishu"],
            inheritProject=True,
            mcpServers={
                "feishu": {
                    "command": ["npx", "lark"],
                    "env": {"APP_ID": "${SWARM_TEST_APP_ID}"},
                }
            },
        ),
    )
    role, _ = parse_role(role_dir)

    assert role is not None
    assert role.mcp_servers["feishu"]["env"]["APP_ID"] == "secret-app-id"
    view = role_view(role)
    assert view["skills"] == ["skills", "extra"]
    assert view["plugins"] == ["zen.feishu"]
    assert view["inheritProject"] is True
    # The editor round-trips the manifest, so it sees placeholders, not secrets.
    assert view["mcpServers"]["feishu"]["env"]["APP_ID"] == "${SWARM_TEST_APP_ID}"


def test_write_role_round_trips_the_full_config_surface(home, proj):
    _write_role("research", _manifest(), root=proj)

    write_role(
        "research",
        {
            "schemaVersion": 1,
            "name": "Research",
            "mode": "ask",
            "systemPrompt": "You research.",
            "skills": ["skills"],
            "plugins": [],
            "inheritProject": True,
            "mcpServers": {"local": {"command": ["echo"]}},
        },
        root=proj,
    )

    role, _ = parse_role(project_agents_root(proj) / "research", project_root=proj)
    assert role is not None
    view = role_view(role)
    assert view["skills"] == ["skills"]
    assert view["plugins"] == []
    assert view["mcpServers"] == {"local": {"command": ["echo"]}}
    assert view["inheritProject"] is True


def test_role_signature_tracks_mcp_values_and_plugins(home):
    role_dir = _write_role(
        "research",
        _manifest(mcpServers={"local": {"command": ["echo", "a"]}}),
    )
    first, _ = parse_role(role_dir)
    assert first is not None

    (role_dir / "manifest.json").write_text(
        json.dumps(
            _manifest(
                plugins=["zen.feishu"],
                mcpServers={"local": {"command": ["echo", "b"]}},
            )
        ),
        encoding="utf-8",
    )
    second, _ = parse_role(role_dir)
    assert second is not None

    assert role_signature(first) != role_signature(second)


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


def test_prompt_file_traversal_is_refused(home):
    role_dir = _write_role("r", {"id": "r", "systemPromptFile": "../secret.txt"})
    (role_dir.parent / "secret.txt").write_text("leak", encoding="utf-8")

    manifest, warnings = parse_role(role_dir)

    assert manifest is not None
    assert manifest.system_prompt == ""
    assert any("prompt" in w for w in warnings)


def test_project_role_prompt_traversal_is_refused(home, proj):
    role_dir = _write_role(
        "r", {"id": "r", "systemPromptFile": "../secret.txt"}, root=proj
    )
    (role_dir.parent / "secret.txt").write_text("leak", encoding="utf-8")

    manifest, warnings = parse_role(role_dir, project_root=proj)

    assert manifest is not None
    assert manifest.system_prompt == ""
    assert any("prompt" in w for w in warnings)


def test_skills_traversal_is_refused(home):
    role_dir = _write_role("r", {"id": "r", "skills": ["../evil"]})
    (role_dir.parent / "evil").mkdir()

    manifest, warnings = parse_role(role_dir)

    assert manifest is not None
    assert manifest.skill_roots == []
    assert any("skills" in w for w in warnings)


def test_copy_role_lands_project_local_and_leaves_library(tmp_path, home):
    lib = agents_root() / "research"
    lib.mkdir(parents=True)
    (lib / "manifest.json").write_text(
        json.dumps(
            {
                "id": "research",
                "name": "R",
                "mcpServers": {"x": {"env": {"K": "${MY_KEY}"}}},
            }
        ),
        encoding="utf-8",
    )
    (lib / "prompt.md").write_text("be nice", encoding="utf-8")
    root = tmp_path / "proj"
    root.mkdir()

    role = copy_role("research", root=root)

    copied = root / ".zenagent" / "agents" / "research"
    assert copied.is_dir()
    assert (copied / "prompt.md").read_text() == "be nice"
    assert (lib / "manifest.json").exists()  # library untouched
    # credential placeholder preserved verbatim on disk
    assert "${MY_KEY}" in (copied / "manifest.json").read_text()
    assert role.id == "research"
    assert role.root == root.resolve()


def test_copy_resets_library_defaults_materialized_by_the_old_editor(tmp_path, home):
    lib = agents_root() / "research"
    lib.mkdir(parents=True)
    (lib / "manifest.json").write_text(
        json.dumps(
            {
                "id": "research",
                "name": "R",
                # The pre-project Settings editor wrote the library's computed
                # defaults back into the manifest.
                "root": str(workspaces_root() / "research"),
                "inheritProject": False,
                "mcpServers": {"x": {"env": {"K": "${MY_KEY}"}}},
            }
        ),
        encoding="utf-8",
    )
    root = tmp_path / "proj"
    root.mkdir()

    role = copy_role("research", root=root)

    copied = json.loads(
        (root / ".zenagent" / "agents" / "research" / "manifest.json").read_text()
    )
    assert "root" not in copied
    assert "inheritProject" not in copied
    assert "${MY_KEY}" in json.dumps(copied)  # credentials stay placeholders
    assert role.root == root.resolve()
    assert role.inherit_project is True


def test_copy_keeps_a_pinned_root_and_its_isolation(tmp_path, home):
    shared = tmp_path / "shared"
    shared.mkdir()
    lib = agents_root() / "research"
    lib.mkdir(parents=True)
    (lib / "manifest.json").write_text(
        json.dumps({"id": "research", "root": str(shared), "inheritProject": False}),
        encoding="utf-8",
    )
    root = tmp_path / "proj"
    root.mkdir()

    role = copy_role("research", root=root)

    assert role.root == shared.resolve()
    assert role.inherit_project is False


def test_copy_refuses_to_clobber(tmp_path, home):
    (agents_root() / "r").mkdir(parents=True)
    (agents_root() / "r" / "manifest.json").write_text(
        json.dumps({"id": "r"}), encoding="utf-8"
    )
    root = tmp_path / "proj"
    (root / ".zenagent" / "agents" / "r").mkdir(parents=True)

    with pytest.raises(ValueError):
        copy_role("r", root=root)
