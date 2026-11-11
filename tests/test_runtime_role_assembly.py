import pytest

from zenagent.agent.protocol import AgentTool, ToolResultAgent
from zenagent.types import TextContent


def _stub_tool(name: str) -> AgentTool:
    async def execute(call_id, args, abort, on_update=None) -> ToolResultAgent:
        return ToolResultAgent(content=[TextContent(text="ok")])

    return AgentTool(
        name=name,
        description="test stub",
        parameters={"type": "object", "properties": {}},
        execute=execute,
        approval="read",
    )


@pytest.mark.asyncio
async def test_tools_allow_filters_assembled_tools(runtime_factory):
    runtime = await runtime_factory(tools_allow=["read_file"])
    assert [t.name for t in runtime.agent.tools] == ["read_file"]


@pytest.mark.asyncio
async def test_extra_tools_are_appended_after_allowlist(runtime_factory):
    runtime = await runtime_factory(
        tools_allow=["read_file"], extra_tools=[_stub_tool("swarm_post")]
    )
    assert [t.name for t in runtime.agent.tools] == ["read_file", "swarm_post"]


@pytest.mark.asyncio
async def test_extra_prompt_reaches_system_prompt(runtime_factory):
    runtime = await runtime_factory(extra_prompt="ROLE-PERSONA")
    assert "ROLE-PERSONA" in runtime.agent.system_prompt


@pytest.mark.asyncio
async def test_plugins_false_skips_global_plugin_assembly(runtime_factory, monkeypatch):
    from zenagent.app import runtime as runtime_module

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("global plugins must not load into an isolated role")

    monkeypatch.setattr(runtime_module.plugin_wiring, "start_plugins", forbidden)
    runtime = await runtime_factory(plugins=False)
    assert runtime.agent is not None
