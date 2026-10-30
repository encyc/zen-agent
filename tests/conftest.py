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

    async def make(*, stream_fn=None, **overrides):
        options = RuntimeOptions(
            mcp=False, skills=False, templates=False, **overrides
        )
        runtime = await Runtime.bootstrap(options, stream_fn=stream_fn)
        created.append(runtime)
        return runtime

    yield make
    for runtime in created:
        await runtime.close()


@pytest.fixture
async def runtime_without_glm_key(runtime_factory):
    """A runtime whose zai provider has no API key configured."""
    return await runtime_factory()


def text_stream(text: str = "ok"):
    """A fake StreamFn that answers with fixed text."""

    async def stream(model, messages, tools, options):
        from zenagent.events import AssistantEvent
        from zenagent.types import AssistantMessage, TextContent, Usage

        partial = AssistantMessage(
            content=[TextContent(text=text)],
            provider="p",
            model="m",
            usage=Usage(),
            stop_reason="pending",
            timestamp=0,
        )
        yield AssistantEvent(type="start", partial=partial)
        partial.stop_reason = "stop"
        yield AssistantEvent(type="done", partial=partial, reason="stop")

    return stream


@pytest.fixture
async def session_runtime(runtime_factory, app_env):
    """Runtime whose sessions live in the temp dir and whose runs are offline."""
    return await runtime_factory(stream_fn=text_stream())


@pytest.fixture
async def services(session_runtime):
    """The container build_default_commands expects."""
    from zenagent.app.chat import ChatController
    from zenagent.app.commands import AppServices
    from zenagent.app.models import ModelManager
    from zenagent.app.sessions import SessionManager

    runtime = session_runtime
    return AppServices(
        runtime=runtime,
        chat=ChatController(runtime),
        models=ModelManager(runtime),
        sessions=SessionManager(runtime),
    )
