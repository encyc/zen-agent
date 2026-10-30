"""Runtime: frontend-agnostic assembly of agent, session, tools and MCP."""

from zenagent.agent import Agent


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


def test_public_embedding_api_is_importable():
    from zenagent.app import (  # noqa: F401
        ChatController,
        CommandRegistry,
        ModelManager,
        PermissionBroker,
        Runtime,
        SessionManager,
    )
