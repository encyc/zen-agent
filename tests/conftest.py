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
        "zenagent.agent.session.sessions_dir",
        lambda *, root=None: tmp_path / "sessions",
    )
    return tmp_path


@pytest.fixture(autouse=True)
def isolated_project_home(tmp_path, monkeypatch):
    """Never read or write the developer's real ~/.zenagent/projects."""
    from zenagent import project

    monkeypatch.setattr(project, "zenagent_home", lambda: tmp_path / "zenagent-home")


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    """Never read or write the developer's real ~/.zenagent/settings.json."""
    from zenagent import settings

    monkeypatch.setenv("ZENAGENT_SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setattr(settings, "config_path", lambda: tmp_path / "no-config.toml")
    settings.clear_cache()
    yield
    settings.clear_cache()


@pytest.fixture(autouse=True)
def isolated_tui_history(tmp_path, monkeypatch):
    """Never write the developer's real ~/.zenagent/tui-history.jsonl."""
    from zenagent.cli.tui import history as history_module

    monkeypatch.setattr(
        history_module, "default_history_path", lambda: tmp_path / "tui-history.jsonl"
    )


@pytest.fixture(autouse=True)
def isolated_plugins_root(tmp_path, monkeypatch):
    """Never discover or install into the developer's real ~/.zenagent/plugins."""
    from zenagent.plugins import discovery

    monkeypatch.setattr(discovery, "installed_root", lambda: tmp_path / "plugins")


@pytest.fixture(autouse=True)
def isolated_update_check(tmp_path, monkeypatch):
    """Update checks never run or touch the real cache file in tests."""
    monkeypatch.setenv("ZENAGENT_NO_UPDATE_CHECK", "1")
    monkeypatch.setenv("ZENAGENT_UPDATE_CACHE_PATH", str(tmp_path / "update.json"))


_GIT_IDENTITY = {
    "GIT_AUTHOR_NAME": "ZenAgent Tests",
    "GIT_AUTHOR_EMAIL": "tests@zenagent.invalid",
    "GIT_COMMITTER_NAME": "ZenAgent Tests",
    "GIT_COMMITTER_EMAIL": "tests@zenagent.invalid",
}


@pytest.fixture(autouse=True)
def isolated_git_identity(monkeypatch):
    """``git commit`` in a test repo never depends on the machine's identity.

    A bare CI runner has no ``user.name``/``user.email`` and a dotless hostname,
    so git cannot even auto-detect an author and the commit dies with exit 128
    ("Author identity unknown") — which is how the worktree-heavy tests went red
    on ubuntu while the identical code stayed green on macOS. The env vars win
    over any config file, so every runner commits the same way.
    """
    for name, value in _GIT_IDENTITY.items():
        monkeypatch.setenv(name, value)


@pytest.fixture
async def runtime_factory(app_env):
    """Build Runtime instances for tests; closes them all on teardown.

    The legacy role kwargs (``mcp``/``skills``/``tools_allow``…) are folded
    into an ``AgentProfile`` here, so tests exercise the same assembly seam the
    production callers use without repeating the profile construction.
    """
    from dataclasses import replace

    from zenagent.app.agent_profile import AgentProfile
    from zenagent.app.runtime import Runtime, RuntimeOptions

    created = []
    profile_keys = {
        "mcp",
        "skills",
        "templates",
        "plugins",
        "context_files",
        "project_sessions",
        "tools_allow",
        "extra_tools",
        "extra_prompt",
    }

    async def make(*, stream_fn=None, **overrides):
        profile = overrides.pop(
            "profile", AgentProfile(mcp=False, skills=False, templates=False)
        )
        changes = {
            key: overrides.pop(key)
            for key in list(overrides)
            if key in profile_keys
        }
        if changes.get("tools_allow") is not None:
            changes["tools_allow"] = tuple(changes["tools_allow"])
        if changes.get("extra_tools") is not None:
            changes["extra_tools"] = tuple(changes["extra_tools"])
        if changes:
            profile = replace(profile, **changes)
        options = RuntimeOptions(profile=profile, **overrides)
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


@pytest.fixture
async def fake_runtime(runtime_factory):
    """Runtime with an offline stream, for TUI pilot tests."""
    return await runtime_factory(stream_fn=text_stream())


def tool_then_text_stream():
    """First turn asks for read_file, second answers — exercises tool cards."""
    from zenagent.events import AssistantEvent
    from zenagent.types import (
        AssistantMessage,
        TextContent,
        ToolCall,
        ToolResultMessage,
        Usage,
    )

    async def stream(model, messages, tools, options):
        partial = AssistantMessage(
            content=[], provider="p", model="m", usage=Usage(),
            stop_reason="pending", timestamp=0,
        )
        yield AssistantEvent(type="start", partial=partial)
        last = messages[-1]
        if isinstance(last, ToolResultMessage):
            partial.content = [TextContent(text="all done")]
            partial.stop_reason = "stop"
            yield AssistantEvent(
                type="text_delta", partial=partial, content_index=0, delta="all done"
            )
            yield AssistantEvent(type="done", partial=partial, reason="stop")
            return
        call = ToolCall(id="c1", name="read_file", arguments={"path": "README.md"})
        partial.content = [call]
        yield AssistantEvent(
            type="toolcall_start", partial=partial, content_index=0, tool_call=call
        )
        yield AssistantEvent(
            type="toolcall_end", partial=partial, content_index=0, tool_call=call
        )
        partial.stop_reason = "tool_use"
        yield AssistantEvent(type="done", partial=partial, reason="tool_use")

    return stream


@pytest.fixture
async def chat_app_with_one_tool(runtime_factory):
    from zenagent.cli.tui.app import ZenAgentApp

    runtime = await runtime_factory(stream_fn=tool_then_text_stream())
    return ZenAgentApp(runtime)


@pytest.fixture
async def gated_app(runtime_factory):
    """App with a stream that stays running until the returned gate is set."""
    import asyncio

    from zenagent.cli.tui.app import ZenAgentApp
    from zenagent.events import AssistantEvent
    from zenagent.types import AssistantMessage, TextContent, Usage

    gate = asyncio.Event()

    async def stream(model, messages, tools, options):
        partial = AssistantMessage(
            content=[TextContent(text="")], provider="p", model="m",
            usage=Usage(), stop_reason="pending", timestamp=0,
        )
        yield AssistantEvent(type="start", partial=partial)
        await gate.wait()
        partial.content[0].text = "done"
        partial.stop_reason = "stop"
        yield AssistantEvent(type="text_delta", partial=partial, content_index=0, delta="done")
        yield AssistantEvent(type="done", partial=partial, reason="stop")

    runtime = await runtime_factory(stream_fn=stream)
    return ZenAgentApp(runtime), gate


@pytest.fixture
async def picker_app(fake_runtime):
    from zenagent.cli.tui.app import ZenAgentApp

    return ZenAgentApp(fake_runtime)


@pytest.fixture
async def narrow_app(fake_runtime):
    from zenagent.cli.tui.app import ZenAgentApp

    return ZenAgentApp(fake_runtime)


@pytest.fixture
async def polish_app(fake_runtime):
    from zenagent.cli.tui.app import ZenAgentApp

    return ZenAgentApp(fake_runtime)


def sequenced_stream(texts: list[str]):
    """One fixed answer per call, in order."""
    remaining = list(texts)

    async def stream(model, messages, tools, options):
        from zenagent.events import AssistantEvent
        from zenagent.types import AssistantMessage, TextContent, Usage

        text = remaining.pop(0) if remaining else "done"
        partial = AssistantMessage(
            content=[TextContent(text=text)], provider="p", model="m",
            usage=Usage(), stop_reason="pending", timestamp=0,
        )
        yield AssistantEvent(type="start", partial=partial)
        partial.stop_reason = "stop"
        yield AssistantEvent(type="done", partial=partial, reason="stop")

    return stream

@pytest.fixture(autouse=True)
def _reset_i18n_locale():
    """Keep the global locale deterministic across tests."""
    from zenagent import i18n

    i18n.set_locale("en")
    yield
    i18n.set_locale("en")
