"""Shared fixtures for app-layer and TUI tests."""

from __future__ import annotations

import pytest

from zenagent.config import UserConfig


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    """Isolated HOME-ish environment: known key, default config, temp sessions."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.delenv("ZAI_CODING_CN_API_KEY", raising=False)
    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
    monkeypatch.setattr(
        "zenagent.app.runtime.load_user_config", lambda *args, **kwargs: UserConfig(default_model="deepseek-flash")
    )
    monkeypatch.setattr(
        "zenagent.agent.session.sessions_dir", lambda: tmp_path / "sessions"
    )
    return tmp_path


@pytest.fixture
async def runtime_factory(app_env):
    """Build Runtime instances for tests; closes them all on teardown."""
    from zenagent.app.runtime import Runtime, RuntimeOptions

    created = []

    async def make(**overrides):
        options = RuntimeOptions(
            mcp=False, skills=False, templates=False, **overrides
        )
        runtime = await Runtime.bootstrap(options)
        created.append(runtime)
        return runtime

    yield make
    for runtime in created:
        await runtime.close()
