"""A launch without an API key opens the TUI instead of refusing to start.

The key is a button-press away in ``/settings``; blocking the whole window on a
missing env var forces the user to configure the machine before they can even
see the setup screen.
"""

import pytest

from zenagent.app.runtime import Runtime, RuntimeOptions
from zenagent.cli.tui.app import ZenAgentApp
from zenagent.cli.tui.widgets.error_banner import ErrorBanner

_OPTIONS = RuntimeOptions(mcp=False, skills=False, templates=False)


class _NoKeys:
    """Running under the keyless path for every test in this module."""


@pytest.fixture(autouse=True)
def _no_keys(app_env, monkeypatch):
    for name in ("DEEPSEEK_API_KEY", "ZAI_CODING_CN_API_KEY", "OPENCODE_API_KEY"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
async def keyless_runtime():
    runtime = await Runtime.bootstrap(_OPTIONS, allow_missing_key=True)
    yield runtime
    await runtime.close()


async def test_a_keyless_launch_opens_the_tui_with_a_hint(keyless_runtime):
    app = ZenAgentApp(keyless_runtime)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()

        banner = app.query_one(ErrorBanner)
        assert banner.display is True
        assert "no API key" in banner.plain
        assert "/settings" in banner.plain
        assert "DEEPSEEK_API_KEY" in banner.plain


async def test_pasting_a_key_in_settings_heals_the_running_app(keyless_runtime):
    from textual.widgets import Input

    from zenagent import settings

    app = ZenAgentApp(keyless_runtime)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        assert app.query_one(ErrorBanner).display is True

        app._prompt_secret("DEEPSEEK_API_KEY", "model provider key")
        await pilot.pause()
        field = app.screen.query_one("#secret-input", Input)
        field.value = "sk-from-tui"
        await pilot.press("enter")
        await pilot.pause()

        assert settings.get_api_key("DEEPSEEK_API_KEY") == "sk-from-tui"
        assert keyless_runtime.agent.api_key == "sk-from-tui"
        assert keyless_runtime.missing_api_key is None
        assert app.query_one(ErrorBanner).display is False
