"""Frontend parity: the headless one-shot run must match the Runtime fronts.

``zen -p`` is assembled by :class:`zenagent.app.runtime.Runtime` like every
other frontend; what is headless-specific is the option set it hands bootstrap
and the output contract around it. Four behaviours used to drift and are pinned
here:

* project memory -- the ``memory``/``memory_write`` tools and the
  ``## Project memory`` prompt block;
* prompt composition -- the skills -> memory -> extension order and the
  byte-equality with a plain Runtime bootstrap on the same discovery results;
* tool policy -- ``ToolRegistry``'s ``hidden``/``default_inactive`` filtering;
* lifecycle -- the run's ``finally`` releases cached LSP clients and kills
  background bash jobs, the way ``Runtime.close()`` does.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import zenagent.cli.main as cli_main
from zenagent import project
from zenagent.agent.memory_store import MemoryStore
from zenagent.config import UserConfig
from zenagent.events import AssistantEvent
from zenagent.types import AssistantMessage, TextContent, Usage

#: The roster differs in order between the fronts (pinned by the golden prompt
#: tests); every other byte of the prompt must match the Runtime's.
ROSTER = re.compile(r"Available tools: [^\n]*")
ROSTER_PLACEHOLDER = "Available tools: <roster>"


@pytest.fixture(autouse=True)
def isolate_user_config(monkeypatch):
    """Tests must not read the developer's real ~/.zenagent/config.toml."""
    monkeypatch.setattr(
        "zenagent.app.runtime.load_user_config", lambda *a, **k: UserConfig()
    )


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """Keep global ~/.zenagent discovery out of the parity assertions."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home


def fake_stream(text: str = "ok"):
    """A StreamFn that emits one text block and stops."""

    async def stream(model, messages, tools, options):
        partial = AssistantMessage(
            content=[],
            provider=model.provider,
            model=model.id,
            usage=Usage(),
            stop_reason="pending",
            timestamp=0,
        )
        yield AssistantEvent(type="start", partial=partial)
        partial.content.append(TextContent(text=text))
        yield AssistantEvent(
            type="text_delta", partial=partial, delta=text, content_index=0
        )
        partial.stop_reason = "stop"
        yield AssistantEvent(type="done", partial=partial, reason="stop")

    return stream


def _record_real_runtime(monkeypatch, captured: dict) -> None:
    """Wrap the real assembly seam and keep the Runtime the run built."""

    from zenagent.app.runtime import Runtime

    real_bootstrap = Runtime.bootstrap.__func__

    async def bootstrap(cls, options, **kwargs):
        runtime = await real_bootstrap(cls, options, **kwargs)
        captured["runtime"] = runtime
        captured["agent"] = runtime.agent
        captured["tools"] = [tool.name for tool in runtime.agent.tools]
        return runtime

    monkeypatch.setattr(Runtime, "bootstrap", classmethod(bootstrap))


async def _run_headless(**overrides):
    """A trivial offline one-shot run of the default assembly."""

    kwargs = {
        "model_id": "deepseek-flash",
        "stream_fn": fake_stream(),
        "mcp": False,
        "skills": False,
    }
    kwargs.update(overrides)
    return await cli_main.run_headless("hi", **kwargs)


async def test_default_assembly_matches_the_runtime_prompt(
    monkeypatch, tmp_path, app_env, runtime_factory, isolated_home
):
    """Memory tools + prompt block reach headless, byte-identical to Runtime."""
    monkeypatch.chdir(tmp_path)
    store = MemoryStore(project.find_project_root(Path.cwd()))
    store.write_entry(
        title="Run make check",
        body="always run make check before pushing",
        source="test",
    )

    captured: dict = {}
    _record_real_runtime(monkeypatch, captured)
    assert await _run_headless() == 0
    agent = captured["agent"]

    assert "memory" in captured["tools"]
    assert "memory_write" in captured["tools"]
    prompt = agent.system_prompt
    assert "## Project memory" in prompt
    assert "Run make check" in prompt
    # The prompt carries the index only; full text stays behind the tool.
    assert "always run make check before pushing" not in prompt

    runtime = await runtime_factory()
    expected = runtime.agent.system_prompt
    assert "## Project memory" in expected
    assert ROSTER.sub(ROSTER_PLACEHOLDER, prompt) == ROSTER.sub(
        ROSTER_PLACEHOLDER, expected
    )


async def test_prompt_order_matches_the_runtime_with_skills_memory_extension(
    monkeypatch, tmp_path, app_env, runtime_factory, isolated_home
):
    """The skills -> memory -> extensions order is the same as compose_system's."""
    monkeypatch.chdir(tmp_path)
    skill_dir = tmp_path / ".zenagent" / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo\ndescription: demo skill for parity\n---\nBody.\n",
        encoding="utf-8",
    )
    store = MemoryStore(project.find_project_root(Path.cwd()))
    store.write_entry(title="Deploy note", body="durable body", source="test")
    extension = tmp_path / "parity_order_ext.py"
    extension.write_text(
        '''
from zenagent.agent.loop import AgentTool, ToolResultAgent
from zenagent.types import TextContent


async def execute(call_id, args, abort, on_update=None):
    return ToolResultAgent(content=[TextContent(text="ok")])


def register(api):
    api.register_tool(AgentTool(
        name="order_tool",
        description="an extension tool",
        parameters={"type": "object"},
        execute=execute,
    ))
    api.add_system_prompt("EXT-ONE: parity fragment")
''',
        encoding="utf-8",
    )

    captured: dict = {}
    _record_real_runtime(monkeypatch, captured)
    assert (
        await _run_headless(skills=True, extension_paths=[str(extension)]) == 0
    )
    prompt = captured["agent"].system_prompt

    assert "## Skills" in prompt
    assert "## Project memory" in prompt
    assert "EXT-ONE: parity fragment" in prompt
    assert prompt.index("## Skills") < prompt.index("## Project memory")
    assert prompt.index("## Project memory") < prompt.index("EXT-ONE: parity fragment")

    runtime = await runtime_factory(
        skills=True, extension_paths=[str(extension)]
    )
    expected = runtime.agent.system_prompt
    assert ROSTER.sub(ROSTER_PLACEHOLDER, prompt) == ROSTER.sub(
        ROSTER_PLACEHOLDER, expected
    )


async def test_empty_memory_library_adds_no_block(
    monkeypatch, tmp_path, app_env, isolated_home
):
    """The memory wiring mirrors Runtime: no hits means no prompt noise."""
    monkeypatch.chdir(tmp_path)
    captured: dict = {}
    _record_real_runtime(monkeypatch, captured)
    assert await _run_headless() == 0
    assert "memory" in captured["tools"]
    assert "## Project memory" not in captured["agent"].system_prompt
    assert not (tmp_path / ".zenagent" / "memory").exists()


async def test_custom_tools_skip_memory_like_the_runtime(
    monkeypatch, tmp_path, app_env, isolated_home
):
    """``tools=...`` is embedding/test seam: no discovery, no memory block."""
    from zenagent.tools.fs import READ_FILE

    monkeypatch.chdir(tmp_path)
    store = MemoryStore(project.find_project_root(Path.cwd()))
    store.write_entry(title="Note", body="body", source="test")
    captured: dict = {}
    _record_real_runtime(monkeypatch, captured)
    assert await _run_headless(tools=[READ_FILE]) == 0
    assert captured["tools"] == ["read_file"]
    assert "## Project memory" not in captured["agent"].system_prompt


async def test_teardown_releases_lsp_clients_and_background_jobs(
    monkeypatch, tmp_path, app_env, isolated_home
):
    """Both process-scoped resources are closed once, after the run."""
    monkeypatch.chdir(tmp_path)
    from zenagent.tools import jobs as jobs_module
    from zenagent.tools import lsp as lsp_module

    calls: list[str] = []
    real_release_job_manager = jobs_module.release_job_manager
    real_release_clients = lsp_module.release_clients

    async def fake_release_clients() -> None:
        calls.append("lsp")
        # Call through: the refcount is process-wide (see below).
        await real_release_clients()

    async def fake_release_job_manager() -> None:
        calls.append("jobs")
        # Call through: the refcount is process-wide, and a stubbed release
        # would leave the manager acquired for every later test.
        await real_release_job_manager()

    monkeypatch.setattr("zenagent.app.runtime.release_clients", fake_release_clients)
    monkeypatch.setattr(
        "zenagent.tools.jobs.release_job_manager", fake_release_job_manager
    )
    captured: dict = {}
    _record_real_runtime(monkeypatch, captured)

    assert await _run_headless() == 0
    # Same order as Runtime.close: LSP first, then the job manager.
    assert calls == ["lsp", "jobs"]


async def test_teardown_failure_does_not_fail_the_run(
    monkeypatch, tmp_path, app_env, isolated_home
):
    """A broken teardown is swallowed; the run's exit code still wins."""
    monkeypatch.chdir(tmp_path)

    async def broken_release_clients() -> None:
        raise RuntimeError("lsp teardown exploded")

    async def broken_release_job_manager() -> None:
        raise RuntimeError("job teardown exploded")

    def no_acquire():
        # A teardown that never completes would otherwise leak the process-wide
        # refcount into the next test.
        return None

    monkeypatch.setattr("zenagent.tools.jobs.acquire_job_manager", no_acquire)
    monkeypatch.setattr("zenagent.tools.lsp.acquire_clients", no_acquire)
    monkeypatch.setattr(
        "zenagent.app.runtime.release_clients", broken_release_clients
    )
    monkeypatch.setattr(
        "zenagent.tools.jobs.release_job_manager", broken_release_job_manager
    )
    captured: dict = {}
    _record_real_runtime(monkeypatch, captured)

    assert await _run_headless() == 0
    assert captured["agent"].termination == "stop"


EXTENSION = '''
from zenagent.agent.loop import AgentTool, ToolResultAgent
from zenagent.types import TextContent


async def execute(call_id, args, abort, on_update=None):
    return ToolResultAgent(content=[TextContent(text="ok")])


def register(api):
    api.register_tool(AgentTool(
        name="visible_tool",
        description="an ordinary extension tool",
        parameters={"type": "object"},
        execute=execute,
    ))
    api.register_tool(AgentTool(
        name="hidden_tool",
        description="an internal tool the model must never see",
        parameters={"type": "object"},
        execute=execute,
        hidden=True,
    ))
    api.register_tool(AgentTool(
        name="inactive_tool",
        description="opt-in tool, off by default",
        parameters={"type": "object"},
        execute=execute,
        default_inactive=True,
    ))
    api.add_system_prompt("EXTENSION PROMPT")
'''


async def test_hidden_and_inactive_extension_tools_are_filtered(
    monkeypatch, tmp_path, app_env, isolated_home
):
    """ToolRegistry policy applies: hidden/default_inactive never reach the model."""
    monkeypatch.chdir(tmp_path)
    extension = tmp_path / "parity_ext.py"
    extension.write_text(EXTENSION, encoding="utf-8")

    captured: dict = {}
    _record_real_runtime(monkeypatch, captured)
    assert await _run_headless(extension_paths=[str(extension)]) == 0

    tools = captured["tools"]
    assert "visible_tool" in tools
    assert "hidden_tool" not in tools
    assert "inactive_tool" not in tools

    prompt = captured["agent"].system_prompt
    assert "visible_tool" in prompt
    assert "hidden_tool" not in prompt
    assert "inactive_tool" not in prompt
    # The extension prompt fragment is still wired at its place in the chain.
    assert "EXTENSION PROMPT" in prompt
