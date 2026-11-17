"""Plugins manager panel: list, toggle via set_enabled, install/uninstall.

Modal screens sit on top of the screen stack, so queries go through
``app.screen`` (the repo-wide idiom, see tests/test_tui_approval.py).
Discovery and the state store are monkeypatched, so no test touches the real
plugin directories.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest
from textual.widgets import OptionList

from zenagent.app.commands import CommandResult
from zenagent.cli.render import render_command_result
from zenagent.cli.tui.app import ZenAgentApp
from zenagent.plugins.manifest import CredentialSpec, PluginManifest


def _manifest(pid="demo", credentials=()) -> PluginManifest:
    return PluginManifest(
        schema_version=1,
        id=pid,
        name="Demo",
        version="1.0",
        description="a demo plugin",
        mcp_servers={},
        skills=None,
        extension=None,
        credentials=tuple(credentials),
        setup_guide=None,
        requires_runtime=None,
        root=Path("/tmp/demo"),
    )


@pytest.fixture
def plugin_world(monkeypatch):
    """One installed (disabled) plugin; state ops recorded, not persisted."""
    from zenagent.plugins import discovery
    from zenagent.plugins import state as plugin_state

    manifest = _manifest()
    monkeypatch.setattr(discovery, "discover_plugins", lambda: ([manifest], []))
    flags = {"demo": False}
    monkeypatch.setattr(plugin_state, "is_enabled", lambda pid: flags.get(pid, False))

    def set_enabled(pid, enabled):
        flags[pid] = enabled

    monkeypatch.setattr(plugin_state, "set_enabled", set_enabled)
    monkeypatch.setattr(plugin_state, "credentials_for", lambda pid: {})
    return SimpleNamespace(manifest=manifest, flags=flags)


async def test_dispatch_plugins_opens_manager_listing_sections(
    fake_runtime, plugin_world
):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await app._dispatch_command("plugins", "")
        await pilot.pause()
        manager = app.screen
        options = list(manager.query_one("#plugins-list", OptionList).options)
        prompts = [str(option.prompt) for option in options]
        assert any("Demo" in prompt and "○" in prompt for prompt in prompts)
        assert any("builtin catalog" in prompt or "内置目录" in prompt for prompt in prompts)


async def test_toggle_enables_and_reapplies_runtime(
    fake_runtime, plugin_world, monkeypatch
):
    applied: list[bool] = []

    async def fake_reconfigure():
        applied.append(True)

    monkeypatch.setattr(fake_runtime, "reconfigure_plugins", fake_reconfigure)
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await app._dispatch_command("plugins", "")
        await pilot.pause()
        manager = app.screen
        manager.query_one("#plugins-list", OptionList).highlighted = (
            manager._option_for_row(0)
        )  # the installed demo row
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        assert plugin_world.flags["demo"] is True
        assert applied == [True]
        prompts = [
            str(option.prompt)
            for option in manager.query_one("#plugins-list", OptionList).options
        ]
        assert any("●" in prompt and "Demo" in prompt for prompt in prompts)


async def test_uninstall_needs_confirm(fake_runtime, plugin_world, monkeypatch):
    removed: list[str] = []
    from zenagent.plugins import install as plugin_install

    monkeypatch.setattr(
        plugin_install, "uninstall_plugin", lambda pid: removed.append(pid)
    )
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await app._dispatch_command("plugins", "")
        await pilot.pause()
        manager = app.screen
        manager.query_one("#plugins-list", OptionList).highlighted = (
            manager._option_for_row(0)
        )
        await pilot.press("x")
        await pilot.pause()
        assert removed == []  # confirm still open
        await pilot.press("y")
        await pilot.pause()
        await pilot.pause()
        assert removed == ["demo"]


async def test_missing_credentials_show_in_row(fake_runtime, monkeypatch):
    from zenagent.plugins import discovery
    from zenagent.plugins import state as plugin_state

    manifest = _manifest(
        credentials=(CredentialSpec(key="DEMO_TOKEN", label="Demo token"),)
    )
    monkeypatch.setattr(discovery, "discover_plugins", lambda: ([manifest], []))
    monkeypatch.setattr(plugin_state, "is_enabled", lambda pid: True)
    monkeypatch.setattr(plugin_state, "credentials_for", lambda pid: {})
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await app._dispatch_command("plugins", "")
        await pilot.pause()
        prompts = [
            str(option.prompt)
            for option in app.screen.query_one("#plugins-list", OptionList).options
        ]
        assert any("needs credentials" in prompt or "缺凭据" in prompt for prompt in prompts)


def test_repl_renders_plugins_as_text_rows(monkeypatch):
    from zenagent.plugins import discovery
    from zenagent.plugins import state as plugin_state

    manifest = _manifest()
    monkeypatch.setattr(discovery, "discover_plugins", lambda: ([manifest], []))
    monkeypatch.setattr(plugin_state, "is_enabled", lambda pid: False)
    monkeypatch.setattr(plugin_state, "credentials_for", lambda pid: {})
    lines: list[str] = []
    render_command_result(
        CommandResult(screen="plugins-manager"),
        name="plugins",
        services=SimpleNamespace(runtime=None),
        print_fn=lines.append,
        keybindings={},
        set_theme=lambda spec: None,
        set_language=lambda code: None,
    )
    text = "\n".join(lines)
    assert "Demo" in text
    assert "builtin catalog:" in text or "内置目录" in text
