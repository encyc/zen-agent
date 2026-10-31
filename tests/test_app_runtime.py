"""Runtime: frontend-agnostic assembly of agent, session, tools and MCP."""

import pytest

from zenagent.agent import Agent
from zenagent.app.runtime import Runtime, RuntimeOptions


def _options() -> RuntimeOptions:
    return RuntimeOptions(mcp=False, skills=False, templates=False)


async def test_bootstrap_wires_agent_and_session(runtime_factory, app_env):
    runtime = await runtime_factory()

    assert isinstance(runtime.agent, Agent)
    assert runtime.agent.tools, "built-in tools must be wired"
    assert runtime.session.path.parent == app_env / "sessions"
    assert runtime.agent.model.provider == "deepseek"


async def test_close_is_idempotent(runtime_factory):
    runtime = await runtime_factory()
    await runtime.close()
    await runtime.close()


async def test_context_files_are_injected(runtime_factory, app_env, monkeypatch):
    monkeypatch.chdir(app_env)
    (app_env / "AGENTS.md").write_text("PROJECT RULE: use tabs.")
    runtime = await runtime_factory()
    assert "PROJECT RULE: use tabs." in runtime.agent.system_prompt


async def test_bootstrap_still_requires_a_key_by_default(app_env, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="missing API key"):
        await Runtime.bootstrap(_options())


async def test_bootstrap_can_open_a_keyless_workbench(app_env, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    runtime = await Runtime.bootstrap(_options(), allow_missing_key=True)
    try:
        assert runtime.missing_api_key == "DEEPSEEK_API_KEY"
        assert runtime.agent.api_key == ""
        assert runtime.model is not None and runtime.model.id.endswith("flash")
    finally:
        await runtime.close()


async def test_reload_api_key_adopts_a_key_saved_in_the_app(app_env, monkeypatch):
    from zenagent import settings

    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    runtime = await Runtime.bootstrap(_options(), allow_missing_key=True)
    try:
        assert runtime.reload_api_key() is False, "nothing to adopt yet"

        settings.set_api_key("DEEPSEEK_API_KEY", "sk-saved")

        assert runtime.reload_api_key() is True
        assert runtime.agent.api_key == "sk-saved"
        assert runtime.missing_api_key is None
    finally:
        await runtime.close()


def test_public_embedding_api_is_importable():
    from zenagent.app import (  # noqa: F401
        ChatController,
        CommandRegistry,
        ModelManager,
        PermissionBroker,
        Runtime,
        SessionManager,
    )
