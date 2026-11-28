"""Builtin MCP servers: the settings switch, the merge order, and the folding.

The builtin layer is the only place where a server definition belongs to the zen
release rather than to the user's ``mcp.json``, so these tests pin the three
properties that choice buys: an absent preference means off, a same-named user
server still wins, and a raw tool list never reaches the model uncounted.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zenagent.mcp import builtin
from zenagent.mcp.config import load_mcp_config, withhold_untrusted_project_servers

COMPUTE_USE = builtin.COMPUTE_USE_ID


@pytest.fixture
def no_layers(monkeypatch, tmp_path):
    """Empty global and project layers, so only the builtin can appear."""
    global_path = tmp_path / "global" / "mcp.json"
    project_path = tmp_path / "project" / ".zenagent" / "mcp.json"
    monkeypatch.setattr("zenagent.mcp.config.config_path", lambda: global_path)
    monkeypatch.setattr(
        "zenagent.project.project_mcp_path", lambda cwd=None: project_path
    )
    return global_path, project_path


def test_builtin_is_off_until_the_user_switches_it_on(no_layers):
    assert builtin.is_builtin_enabled(COMPUTE_USE) is False
    assert builtin.builtin_configs() == {}
    assert COMPUTE_USE not in load_mcp_config()


def test_enable_writes_only_a_preference(no_layers):
    global_path, _ = no_layers
    builtin.enable_builtin(COMPUTE_USE)

    assert builtin.is_builtin_enabled(COMPUTE_USE) is True
    servers = load_mcp_config()
    assert servers[COMPUTE_USE].command == [
        "npx",
        "-y",
        f"agent-desktop@{builtin.COMPUTE_USE_VERSION}",
        "mcp",
    ]
    # The pin must never be written into a user file: a copy there would survive
    # a zen upgrade that moves agent-desktop forward.
    assert not global_path.exists()

    builtin.disable_builtin(COMPUTE_USE)
    assert load_mcp_config() == {}


def test_a_user_definition_still_wins_over_the_builtin(no_layers):
    """The escape hatch: someone wants their own agent-desktop command line."""
    global_path, _ = no_layers
    builtin.enable_builtin(COMPUTE_USE)
    global_path.parent.mkdir(parents=True, exist_ok=True)
    global_path.write_text(
        '{"servers": {"compute-use": {"command": ["/usr/local/bin/agent-desktop", "mcp"]}}}'
    )

    servers = load_mcp_config()
    assert servers[COMPUTE_USE].command == ["/usr/local/bin/agent-desktop", "mcp"]


def test_an_explicit_mcp_file_disables_builtins(no_layers, tmp_path):
    """``--mcp-file`` means "exactly this file", never "plus the builtins"."""
    builtin.enable_builtin(COMPUTE_USE)
    explicit = tmp_path / "explicit.json"
    explicit.write_text('{"servers": {"only": {"command": ["only"]}}}')

    servers = load_mcp_config(explicit)
    assert set(servers) == {"only"}


def test_an_untrusted_project_override_falls_back_to_the_builtin(no_layers):
    """A hostile checkout cannot take Computer Use down by shadowing its name."""
    _, project_path = no_layers
    builtin.enable_builtin(COMPUTE_USE)
    project_path.parent.mkdir(parents=True, exist_ok=True)
    project_path.write_text('{"servers": {"compute-use": {"command": ["evil"]}}}')

    servers, untrusted = withhold_untrusted_project_servers(load_mcp_config())
    assert untrusted == [COMPUTE_USE]
    assert servers[COMPUTE_USE].command[0] == "npx"


def test_unknown_builtin_is_rejected():
    with pytest.raises(ValueError):
        builtin.set_builtin_enabled("not-a-server", True)


def test_the_pin_is_a_single_constant():
    """A version bump must be one edit; the definition reads it, never a literal."""
    server = builtin.BUILTIN_SERVERS[COMPUTE_USE]
    assert builtin.COMPUTE_USE_VERSION == server.version
    assert f"agent-desktop@{builtin.COMPUTE_USE_VERSION}" in server.command
    assert server.proxy_tool == "desktop"


def test_the_row_carries_no_command_line(no_layers):
    row = builtin.builtin_row(COMPUTE_USE)
    assert row["label"] == "Computer Use"
    assert row["requires_runtime"].startswith("Node.js")
    assert "command" not in row


def test_inline_import_keeps_config_and_builtin_acyclic():
    """``config`` imports ``builtin``; the reverse must stay function-local."""
    source = (
        Path(__file__).resolve().parents[1] / "zenagent" / "mcp" / "builtin.py"
    ).read_text()
    assert "from zenagent.mcp.config import" in source
    assert "\nfrom zenagent.mcp.config import" not in source.split("def config")[0]
