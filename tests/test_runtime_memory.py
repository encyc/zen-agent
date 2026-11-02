"""End-to-end: the runtime wires the memory store, tools and prompt index."""

from pathlib import Path

from zenagent.agent.memory_store import MEMORY_DIR, MemoryStore


def _tool(runtime, name):
    return next(tool for tool in runtime.agent.tools if tool.name == name)


async def test_runtime_wires_memory_tools_and_store(runtime_factory, app_env, monkeypatch):
    monkeypatch.chdir(app_env)
    runtime = await runtime_factory()
    assert isinstance(runtime.memory_store, MemoryStore)
    assert runtime.memory_store.root == Path(app_env).resolve()
    assert _tool(runtime, "memory") is not None
    assert _tool(runtime, "memory_write") is not None


async def test_add_then_recompose_reflects_in_prompt(runtime_factory, app_env, monkeypatch):
    monkeypatch.chdir(app_env)
    runtime = await runtime_factory()
    assert "## Project memory" not in runtime.agent.system_prompt  # 空库无块

    store = runtime.memory_store
    store.write_entry(title="测试入口", body="always run scripts/check.sh", source="test")
    runtime.reconcile()
    prompt = runtime.agent.system_prompt
    assert "## Project memory" in prompt
    assert "测试入口" in prompt
    # 全文不进 prompt
    assert "always run scripts/check.sh" not in prompt


async def test_empty_project_has_no_memory_noise(runtime_factory, app_env, monkeypatch):
    monkeypatch.chdir(app_env)
    runtime = await runtime_factory()
    assert not (app_env / MEMORY_DIR).exists()  # 只读装配不落目录
    assert "## Project memory" not in runtime.agent.system_prompt
    assert not [w for w in runtime.warnings if "memory" in w.lower()]
