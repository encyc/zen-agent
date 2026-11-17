"""Secrets manager panel: list, set via SecretInput, clear via Confirm, probe.

Modal screens sit on top of the screen stack, so queries go through
``app.screen`` (the repo-wide idiom, see tests/test_tui_approval.py).
Settings live behind ``ZENAGENT_SETTINGS_PATH`` so tests never touch the real
``~/.zenagent`` store.
"""

from types import SimpleNamespace

from textual.widgets import Input, OptionList

from zenagent.app.commands import CommandResult
from zenagent.cli.render import render_command_result
from zenagent.cli.tui.app import ZenAgentApp


def _isolated_store(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ZENAGENT_SETTINGS_PATH", str(tmp_path / "settings.json"))


async def test_dispatch_secrets_opens_manager_listing_providers(
    fake_runtime, tmp_path, monkeypatch
):
    _isolated_store(tmp_path, monkeypatch)
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await app._dispatch_command("secrets", "")
        await pilot.pause()
        manager = app.screen
        options = list(manager.query_one("#secrets-list", OptionList).options)
        catalog_count = len(app.runtime.catalog)
        # One header per section plus one row per provider.
        assert len(options) == catalog_count + 3 + 2
        prompts = [str(option.prompt) for option in options]
        # The env-backed provider is configured (app_env sets its key).
        assert any("●" in prompt for prompt in prompts)
        assert any("TAVILY_API_KEY" in prompt for prompt in prompts)


async def test_set_key_via_secret_input_writes_store(
    fake_runtime, tmp_path, monkeypatch
):
    _isolated_store(tmp_path, monkeypatch)
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await app._dispatch_command("secrets", "")
        await pilot.pause()
        manager = app.screen
        manager.query_one("#secrets-list", OptionList).highlighted = len(
            manager._collect()
        ) - 2  # a KEY_ENVS row (deepseek/tavily/brave order → tavily)
        row = manager._selected()
        assert row is not None and row.env == "TAVILY_API_KEY"
        await pilot.press("s")
        await pilot.pause()
        secret = app.screen
        secret.query_one("#secret-input", Input).value = " tav-key "
        await pilot.press("enter")
        await pilot.pause()
        from zenagent import settings

        assert settings.get_api_key("TAVILY_API_KEY") == "tav-key"
        prompts = [
            str(option.prompt)
            for option in manager.query_one("#secrets-list", OptionList).options
        ]
        assert any("●" in prompt and "TAVILY_API_KEY" in prompt for prompt in prompts)


async def test_clear_requires_confirm_and_empties_key(
    fake_runtime, tmp_path, monkeypatch
):
    _isolated_store(tmp_path, monkeypatch)
    from zenagent import settings

    settings.set_api_key("BRAVE_API_KEY", "brave-key")
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await app._dispatch_command("secrets", "")
        await pilot.pause()
        manager = app.screen
        manager.query_one("#secrets-list", OptionList).highlighted = len(
            manager._collect()
        ) - 1  # the last KEY_ENVS row (brave)
        await pilot.press("x")
        await pilot.pause()
        await pilot.press("y")  # Confirm
        await pilot.pause()
        stored = settings.load().api_keys.get("BRAVE_API_KEY")
        assert stored in (None, "")
        assert settings.get_api_key("BRAVE_API_KEY") is None


async def test_apply_secret_reloads_only_the_current_model_key(
    fake_runtime, tmp_path, monkeypatch
):
    _isolated_store(tmp_path, monkeypatch)
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)):
        recovered = app._apply_secret("DEEPSEEK_API_KEY", "stored-key")
        assert recovered is True
        assert app.runtime.missing_api_key is None
        # Clearing the stored key still recovers while the env fallback exists.
        assert app._apply_secret("DEEPSEEK_API_KEY", "") is True
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        assert app._apply_secret("DEEPSEEK_API_KEY", "") is False
        assert app.runtime.missing_api_key == "DEEPSEEK_API_KEY"
        # A search key never claims to heal the model session.
        assert app._apply_secret("TAVILY_API_KEY", "tav") is False
        assert app.runtime.missing_api_key == "DEEPSEEK_API_KEY"


async def test_test_action_runs_the_right_probe(
    fake_runtime, tmp_path, monkeypatch
):
    _isolated_store(tmp_path, monkeypatch)
    from zenagent.app import probes

    calls: list[tuple[str, str]] = []

    async def fake_probe(catalog, provider_id, **kwargs):
        calls.append(("model", provider_id))
        return probes.ProbeResult(True, "ok")

    monkeypatch.setattr(probes, "probe_model_provider", fake_probe)
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await app._dispatch_command("secrets", "DEEPSEEK_API_KEY")
        await pilot.pause()
        manager = app.screen
        assert manager.preselect == ""  # consumed on first refresh
        await pilot.press("t")
        await pilot.pause()
        await pilot.pause()
        assert calls == [("model", "deepseek")]


def test_repl_renders_secrets_as_text_rows(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    from zenagent import settings

    settings.set_api_key("TAVILY_API_KEY", "tav")
    # A bare namespace is enough: the renderer only reads runtime.catalog.
    runtime = SimpleNamespace(catalog=_mini_catalog(), model=None)
    lines: list[str] = []
    render_command_result(
        CommandResult(screen="secrets-manager"),
        name="secrets",
        services=SimpleNamespace(runtime=runtime),
        print_fn=lines.append,
        keybindings={},
        set_theme=lambda spec: None,
        set_language=lambda code: None,
    )
    text = "\n".join(lines)
    assert "DEEPSEEK_API_KEY" in text
    assert "TAVILY_API_KEY" in text
    assert "●" in text and " " in text
    assert "settings.json" in text


def _mini_catalog():
    """Two providers, enough for the row renderer."""

    class Provider:
        def __init__(self, pid, name, env, models):
            self.id = pid
            self.name = name
            self.api_key_env = env
            self.models = models

    return {
        "deepseek": Provider("deepseek", "DeepSeek", "DEEPSEEK_API_KEY", ["m1", "m2"]),
    }
