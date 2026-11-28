"""The ``/desktop`` command and the ``/mcp`` builtin line.

Both are the read-only surface of the builtin layer: the desktop settings page
owns the real switch, and these exist so a terminal user can see what is on and
open the escape hatch. The folding itself is covered by
``tests/test_desktop_tool.py``; here the assertions are about *text* — the user
must be able to find Computer Use from ``/mcp`` without opening Settings.
"""

from __future__ import annotations

import pytest

from zenagent.app.desktop_commands import desktop_command, desktop_mcp_line
from zenagent.mcp import builtin


class _Status:
    def __init__(self, name: str, state: str, tools: list[str], error: str | None = None):
        self.name = name
        self.state = state
        self.tools = tools
        self.error = error


class _Manager:
    def __init__(self, statuses: list[_Status]):
        self._statuses = statuses
        self.connected_names = [s.name for s in statuses if s.state == "connected"]

    def status(self) -> list[_Status]:
        return self._statuses


class _Runtime:
    def __init__(self, manager=None, *, streaming: bool = False):
        self.mcp = manager
        self._builtin_hidden_tools = ["mcp__compute-use__desktop_click"]
        self.desktop_raw = False
        self.recomposed = 0
        self.agent = type("A", (), {"is_streaming": streaming, "tools": []})()

    def _recompose(self) -> None:
        self.recomposed += 1


class _Services:
    def __init__(self, runtime: _Runtime):
        self.runtime = runtime


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch, tmp_path):
    from zenagent import settings

    monkeypatch.setenv("ZENAGENT_SETTINGS_PATH", str(tmp_path / "settings.json"))
    settings.clear_cache()
    yield
    settings.clear_cache()


def test_mcp_shows_nothing_when_the_builtin_is_off():
    assert desktop_mcp_line(_Runtime(_Manager([]))) is None


def test_mcp_shows_the_builtin_row_when_it_is_on():
    builtin.enable_builtin(builtin.COMPUTE_USE_ID)
    line = desktop_mcp_line(_Runtime(_Manager([_Status("compute-use", "connected", ["a"] * 75)])))

    assert line is not None
    assert "Computer Use" in line
    assert builtin.COMPUTE_USE_VERSION in line
    assert "connected" in line
    assert "75" in line


async def test_status_reports_off_then_connected():
    off = await desktop_command(_Services(_Runtime(None)), "")
    assert "disabled" in off.message.lower()

    builtin.enable_builtin(builtin.COMPUTE_USE_ID)
    on = await desktop_command(
        _Services(_Runtime(_Manager([_Status("compute-use", "connected", ["a"] * 3)]))),
        "status",
    )
    assert "connected" in on.message
    assert "3" in on.message


async def test_status_of_an_enabled_but_failed_server_says_failed():
    builtin.enable_builtin(builtin.COMPUTE_USE_ID)
    result = await desktop_command(
        _Services(_Runtime(_Manager([_Status("compute-use", "failed", [], "npx not found")]))),
        "status",
    )
    assert "npx not found" in result.message


async def test_raw_on_recomposes_and_never_persists():
    from zenagent import settings

    runtime = _Runtime(_Manager([]))
    result = await desktop_command(_Services(runtime), "raw on")

    assert runtime.desktop_raw is True
    assert runtime.recomposed == 1
    assert "raw" in result.message.lower() or "工具" in result.message
    # A per-session escape hatch must not leave a preference behind.
    assert settings.get_pref(builtin.BUILTIN_STATE_PREF) is None


async def test_raw_needs_an_explicit_on_or_off():
    result = await desktop_command(_Services(_Runtime(_Manager([]))), "raw")
    assert "raw on|off" in result.message


async def test_raw_refuses_while_streaming():
    runtime = _Runtime(_Manager([]), streaming=True)
    with pytest.raises(RuntimeError):
        await desktop_command(_Services(runtime), "raw on")


async def test_an_unknown_verb_prints_usage():
    result = await desktop_command(_Services(_Runtime(_Manager([]))), "teleport")
    assert "usage" in result.message.lower() or "用法" in result.message
