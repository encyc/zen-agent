"""Project-layer MCP servers are committed files that spawn commands.

A malicious or compromised repo can write ``.zenagent/mcp.json``; without a
trust step the next launch silently executes its command (audit P1-1). The
contract: project-layer *stdio* servers need an explicit, per-name trust;
HTTP servers and the user's own global layer are untouched.
"""

import json
from pathlib import Path

import pytest


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    """Project with a mcp.json; machine state + global config redirected."""
    root = tmp_path / "proj"
    (root / ".git").mkdir(parents=True)
    (root / ".zenagent").mkdir()
    monkeypatch.setattr("zenagent.mcp.config.project_dir", lambda cwd=None, root=None: tmp_path / "state")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))
    return root


def _project_server(root: Path, name: str, *, stdio: bool = True) -> None:
    target = root / ".zenagent" / "mcp.json"
    payload = (
        {"command": ["python", "-c", "print('pwned')"], "env": {}}
        if stdio
        else {"url": "https://example.test/mcp", "headers": {}}
    )
    target.write_text(json.dumps({"servers": {name: payload}}), encoding="utf-8")


def _global_server(tmp_path: Path, name: str) -> None:
    home = tmp_path / "home"
    (home / ".zenagent").mkdir(parents=True, exist_ok=True)
    (home / ".zenagent" / "mcp.json").write_text(
        json.dumps({"servers": {name: {"url": "https://global.test/mcp", "headers": {}}}}),
        encoding="utf-8",
    )


def test_untrusted_project_stdio_server_is_withheld(isolated):
    from zenagent.mcp.config import load_mcp_config, withhold_untrusted_project_servers

    _project_server(isolated, "evil")
    servers = load_mcp_config(cwd=isolated)
    assert "evil" in servers  # loading is unaffected; withholding is the gate

    kept, untrusted = withhold_untrusted_project_servers(servers, cwd=isolated)
    assert "evil" not in kept
    assert untrusted == ["evil"]


def test_trusted_project_server_is_kept(isolated):
    from zenagent.mcp.config import (
        load_mcp_config,
        trust_project_server,
        withhold_untrusted_project_servers,
    )

    _project_server(isolated, "evil")
    trust_project_server("evil", cwd=isolated)

    kept, untrusted = withhold_untrusted_project_servers(
        load_mcp_config(cwd=isolated), cwd=isolated
    )
    assert "evil" in kept
    assert untrusted == []


def test_global_servers_are_never_withheld(isolated):
    from zenagent.mcp.config import load_mcp_config, withhold_untrusted_project_servers

    _global_server(isolated.parent, "global-http")
    servers = load_mcp_config(cwd=isolated)
    assert "global-http" in servers

    kept, untrusted = withhold_untrusted_project_servers(servers, cwd=isolated)
    assert kept == servers
    assert untrusted == []


def test_withheld_project_override_restores_the_global_definition(isolated):
    """A project override must not take the user's global server down with it."""
    from zenagent.mcp.config import load_mcp_config, withhold_untrusted_project_servers

    _global_server(isolated.parent, "dup")
    _project_server(isolated, "dup")  # stdio override of the global http server
    servers = load_mcp_config(cwd=isolated)
    assert servers["dup"].command is not None  # the project layer won the merge

    kept, untrusted = withhold_untrusted_project_servers(servers, cwd=isolated)
    assert "dup" in kept
    assert kept["dup"].url == "https://global.test/mcp"  # global restored
    assert untrusted == ["dup"]


def test_http_project_server_needs_no_trust(isolated):
    from zenagent.mcp.config import load_mcp_config, withhold_untrusted_project_servers

    _project_server(isolated, "remote", stdio=False)
    kept, untrusted = withhold_untrusted_project_servers(
        load_mcp_config(cwd=isolated), cwd=isolated
    )
    assert "remote" in kept
    assert untrusted == []


async def test_plugin_reconfigure_keeps_the_trust_gate(
    isolated, runtime_factory
):
    """A plugin reload goes through the same project trust gate as bootstrap.

    reconfigure used to call load_mcp_config without the withhold step, so
    'reload plugins' in a hostile repo silently spawned the stdio command
    bootstrap had withheld (final-review finding)."""
    from zenagent.app import plugin_wiring

    _project_server(isolated, "evil")
    runtime = await runtime_factory(mcp=True, plugins=True, root=isolated)
    # Bootstrap withheld it: nothing running, and the note names the fix.
    assert runtime.mcp is None
    assert any("'evil' is not trusted" in w for w in runtime.warnings)
    runtime.warnings.clear()

    await plugin_wiring.reconfigure(runtime)
    # The reload must not silently respawn the withheld server either.
    assert runtime.mcp is None
    assert any("'evil' is not trusted" in w for w in runtime.warnings)
