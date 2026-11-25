"""Limits panel: registry listing, edit via ValueInput, reset via Confirm.

Modal screens sit on top of the screen stack, so queries go through
``app.screen`` (the repo-wide idiom, see tests/test_tui_approval.py).
The config file lives behind a patched ``zenagent.config.config_path`` (the
same isolation ``tests/test_limits_rpc.py`` uses), so no test touches the
real ``~/.zenagent/config.toml``.
"""

from types import SimpleNamespace

import pytest
from textual.widgets import Input, OptionList

from zenagent.app.commands import CommandResult
from zenagent.cli.render import render_command_result
from zenagent.cli.tui.app import ZenAgentApp
from zenagent.limits import read_managed


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    monkeypatch.setattr("zenagent.config.config_path", lambda: path)
    return path


def _select(manager, key: str) -> None:
    index = next(i for i, row in enumerate(manager._rows) if row.key == key)
    manager.query_one("#limits-list", OptionList).highlighted = (
        manager._option_for_row(index)
    )


async def test_dispatch_limits_lists_the_registry(fake_runtime, config_file):
    from zenagent.limits import ALL_KNOBS

    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await app._dispatch_command("limits", "")
        await pilot.pause()
        manager = app.screen
        assert len(manager._rows) == len(ALL_KNOBS)
        options = list(manager.query_one("#limits-list", OptionList).options)
        assert any(
            "limits.agent.loop_guard_threshold" in str(option.prompt) for option in options
        )


async def test_edit_writes_managed_table_and_adopts_live(fake_runtime, config_file):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await app._dispatch_command("limits", "")
        await pilot.pause()
        manager = app.screen
        _select(manager, "limits.agent.loop_guard_threshold")
        await pilot.press("enter")
        await pilot.pause()
        editor = app.screen
        editor.query_one("#value-input", Input).value = "42"
        await pilot.press("enter")
        await pilot.pause()
        managed = read_managed(config_file)
        assert managed["limits.agent"]["loop_guard_threshold"] == 42
        # reload_into pushed the file into the live session's config.
        assert app.runtime.user_config.limits.value("loop_guard_threshold") == 42
        row = next(
            r for r in manager._rows if r.key == "limits.agent.loop_guard_threshold"
        )
        assert row.explicit is True and row.value == 42


async def test_reset_removes_the_managed_key(fake_runtime, config_file):
    from zenagent.limits import write_managed

    write_managed(config_file, {"limits.agent": {"loop_guard_threshold": 7}})
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await app._dispatch_command("limits", "")
        await pilot.pause()
        manager = app.screen
        _select(manager, "limits.agent.loop_guard_threshold")
        await pilot.press("r")
        await pilot.pause()
        await pilot.press("y")  # Confirm
        await pilot.pause()
        assert "loop_guard_threshold" not in read_managed(config_file).get("limits.agent", {})


async def test_edit_rejects_out_of_range(fake_runtime, config_file):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await app._dispatch_command("limits", "")
        await pilot.pause()
        manager = app.screen
        _select(manager, "limits.agent.loop_guard_threshold")
        await pilot.press("enter")
        await pilot.pause()
        editor = app.screen
        editor.query_one("#value-input", Input).value = "999999"
        await pilot.press("enter")
        await pilot.pause()
        assert read_managed(config_file) == {}


def test_repl_renders_limits_grouped_by_table(config_file):
    lines: list[str] = []
    render_command_result(
        CommandResult(screen="limits-manager"),
        name="limits",
        services=SimpleNamespace(runtime=None),
        print_fn=lines.append,
        keybindings={},
        set_theme=lambda spec: None,
        set_language=lambda code: None,
    )
    text = "\n".join(lines)
    assert "limits.agent:" in text
    assert "limits.agent.loop_guard_threshold" in text
    assert str(config_file) in text
