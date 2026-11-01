"""ToolRegistry: hidden, default_inactive, overrides and one-shot reconcile."""

import pytest

from zenagent.agent.loop import AgentTool, ToolResultAgent
from zenagent.agent.registry import ToolRegistry, tool_fingerprint
from zenagent.types import TextContent


async def _noop(call_id, args, abort=None, on_update=None) -> ToolResultAgent:
    return ToolResultAgent(content=[TextContent(text="ok")])


def tool(
    name: str,
    *,
    description: str = "d",
    parameters: dict | None = None,
    hidden: bool = False,
    default_inactive: bool = False,
) -> AgentTool:
    return AgentTool(
        name=name,
        description=description,
        parameters=parameters or {"type": "object"},
        execute=_noop,
        approval="read",
        hidden=hidden,
        default_inactive=default_inactive,
    )


def test_hidden_tool_is_never_active():
    registry = ToolRegistry([tool("visible"), tool("secret", hidden=True)])
    assert registry.active_names() == ["visible"]
    with pytest.raises(ValueError, match="hidden"):
        registry.set_active("secret", True)


def test_default_inactive_tool_waits_for_activation():
    registry = ToolRegistry([tool("always"), tool("opt_in", default_inactive=True)])
    assert registry.active_names() == ["always"]
    assert registry.set_active("opt_in", True) is True
    assert registry.active_names() == ["always", "opt_in"]
    assert registry.set_active("opt_in", True) is False  # already active: no-op
    assert registry.set_active("opt_in", False) is True
    assert registry.active_names() == ["always"]


def test_set_active_rejects_unknown_tools():
    with pytest.raises(KeyError):
        ToolRegistry().set_active("ghost", True)


def test_fingerprint_ignores_the_execute_identity():
    first, second = tool("read"), tool("read")
    assert tool_fingerprint(first) == tool_fingerprint(second)
    assert tool_fingerprint(tool("read", description="other")) != tool_fingerprint(first)


def test_reconcile_reports_model_visible_changes_once():
    registry = ToolRegistry([tool("a")])
    seen: list[int] = []
    registry.subscribe(lambda _registry: seen.append(_registry.version))

    # Same tool re-created (new callable) is not a model-visible change.
    assert registry.reconcile([tool("a")]) is False
    assert seen == []

    # A new active tool changes the set: one notification.
    assert registry.reconcile([tool("a"), tool("b")]) is True
    assert len(seen) == 1

    # A hidden tool appearing changes the registry but not what the model sees.
    assert registry.reconcile([tool("a"), tool("b"), tool("c", hidden=True)]) is False
    assert len(seen) == 1

    # A schema change on an active tool is a model-visible change.
    assert (
        registry.reconcile(
            [tool("a"), tool("b", parameters={"type": "object", "required": ["x"]})]
        )
        is True
    )
    assert len(seen) == 2


def test_reconcile_keeps_overrides_for_surviving_tools_and_drops_stale_ones():
    registry = ToolRegistry([tool("opt", default_inactive=True), tool("gone", default_inactive=True)])
    registry.set_active("opt", True)
    registry.set_active("gone", True)
    assert registry.active_names() == ["opt", "gone"]

    # "gone" disappears; "opt" stays active despite its default_inactive flag.
    registry.reconcile([tool("opt", default_inactive=True)])
    assert registry.active_names() == ["opt"]

    # Re-adding "gone" starts from its declaration again (override was pruned).
    registry.reconcile([tool("opt", default_inactive=True), tool("gone", default_inactive=True)])
    assert registry.active_names() == ["opt"]


async def test_runtime_hides_hidden_extension_tools_from_the_model(runtime_factory):
    runtime = await runtime_factory()
    visible = tool("ext_visible")
    secret = tool("ext_hidden", hidden=True)
    runtime._extension_tools = [visible, secret]
    runtime._recompose()

    names = [entry.name for entry in runtime.agent.tools]
    assert "ext_visible" in names
    assert "ext_hidden" not in names
    assert "ext_hidden" not in runtime.agent.system_prompt


async def test_runtime_activates_a_default_inactive_tool_on_request(runtime_factory):
    runtime = await runtime_factory()
    opt_in = tool("ext_opt_in", default_inactive=True)
    runtime._extension_tools = [opt_in]
    runtime._recompose()
    assert "ext_opt_in" not in [entry.name for entry in runtime.agent.tools]

    assert runtime.tool_registry.set_active("ext_opt_in", True) is True
    runtime._recompose()
    assert "ext_opt_in" in [entry.name for entry in runtime.agent.tools]
