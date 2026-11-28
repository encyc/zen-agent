"""The ``desktop`` proxy tool: action dispatch, argument mapping, read-only tiers.

The proxy exists because ``agent-desktop`` publishes 75 tools (~44 KB of
schema) and never declares ``readOnlyHint``. These tests pin what the fold must
keep true: one tool name, upstream names dispatched verbatim, unknown actions
refused before they reach the wire, and the read-only tiers honoured by the
permission gate so a snapshot never prompts.
"""

from __future__ import annotations

import pytest

from zenagent.app.permissions import PermissionGate, tool_is_read_only
from zenagent.tools.desktop import (
    DESKTOP_ACTIONS,
    READ_ONLY_ACTIONS,
    build_desktop_tool,
)


class FakeManager:
    """Records calls and replays canned MCP results."""

    def __init__(self, *, connected: bool = True, error: Exception | None = None):
        self.connected = connected
        self.error = error
        self.calls: list[tuple[str, str, dict]] = []
        self.results: dict[str, dict] = {}

    async def call(self, server: str, tool: str, arguments: dict, abort=None):
        from zenagent.mcp.client import McpCallResult

        self.calls.append((server, tool, arguments))
        if self.error is not None:
            raise self.error
        payload = self.results.get(tool, {"content": [{"type": "text", "text": "ok"}]})
        return McpCallResult(
            content=payload["content"], is_error=payload.get("isError", False)
        )


def tool_for(manager, version: str = "1.3.1"):
    return build_desktop_tool(lambda: manager, version=version)


def test_one_tool_with_a_small_schema():
    tool = tool_for(FakeManager())
    assert tool.name == "desktop"
    assert tool.approval == "write"
    assert tool.read_only_arg == "action"
    assert len(str(tool.parameters)) < 4000
    assert set(tool.parameters["properties"]) >= {"action", "ref", "query", "app"}


def test_the_action_enum_is_the_documented_surface():
    schema = tool_for(FakeManager()).parameters
    assert tuple(schema["properties"]["action"]["enum"]) == DESKTOP_ACTIONS
    assert set(READ_ONLY_ACTIONS) < set(DESKTOP_ACTIONS)


async def test_an_action_dispatches_to_its_upstream_tool():
    manager = FakeManager()
    result = await tool_for(manager).execute("c1", {"action": "snapshot", "app": "Finder"}, None)
    assert not result.is_error
    server, name, arguments = manager.calls[0]
    assert (server, name) == ("compute-use", "desktop_snapshot")
    assert arguments == {"app": "Finder"}


async def test_argument_aliases_map_onto_the_upstream_names():
    manager = FakeManager()
    await tool_for(manager).execute(
        "c1",
        {"action": "click", "ref": "@s1:e8", "query": "Save"},
        None,
    )
    assert manager.calls[0][2] == {"ref_id": "@s1:e8", "text": "Save"}


async def test_undeclared_arguments_never_reach_the_wire():
    """A model must not smuggle a flag this tool never advertised."""
    manager = FakeManager()
    await tool_for(manager).execute(
        "c1", {"action": "snapshot", "app": "Finder", "no_attach": True}, None
    )
    assert manager.calls[0][2] == {"app": "Finder"}


async def test_an_unknown_action_is_refused_before_the_call():
    manager = FakeManager()
    result = await tool_for(manager).execute("c1", {"action": "teleport"}, None)
    assert result.is_error
    assert "unknown action" in result.content[0].text
    assert manager.calls == []


async def test_a_missing_server_explains_itself():
    result = await tool_for(None).execute("c1", {"action": "snapshot"}, None)
    assert result.is_error
    assert "Settings > MCP" in result.content[0].text


async def test_help_fetches_the_version_matched_guide_once():
    manager = FakeManager()
    manager.results["desktop_skills_get"] = {
        "content": [{"type": "text", "text": "GUIDE BODY"}]
    }
    tool = tool_for(manager)

    first = await tool.execute("c1", {"action": "help"}, None)
    second = await tool.execute("c2", {"action": "help"}, None)

    assert first.content[0].text == "GUIDE BODY"
    assert second.content[0].text == "GUIDE BODY"
    assert len(manager.calls) == 1  # cached by server version


async def test_an_mcp_error_becomes_an_error_result():
    from zenagent.mcp.manager import McpError

    manager = FakeManager(error=McpError("STALE_REF: take a new snapshot"))
    result = await tool_for(manager).execute("c1", {"action": "click", "ref": "@s1:e8"}, None)
    assert result.is_error
    assert "STALE_REF" in result.content[0].text


async def test_a_huge_snapshot_is_clipped():
    manager = FakeManager()
    manager.results["desktop_snapshot"] = {
        "content": [{"type": "text", "text": "x" * 100_000}]
    }
    result = await tool_for(manager).execute("c1", {"action": "snapshot"}, None)
    body = result.content[0].text
    assert result.details["truncated"] is True
    assert len(body) < 26_000


@pytest.mark.parametrize("action", READ_ONLY_ACTIONS)
def test_read_only_actions_never_need_confirmation(action: str):
    """A snapshot in plan mode must work; a click must not."""
    tool = tool_for(FakeManager())
    assert tool_is_read_only("desktop", tool, {"action": action}) is True
    verdict = _gate().assess("desktop", {"action": action}, tool)
    assert verdict.kind == "allow"


@pytest.mark.parametrize("action", ["click", "type", "press", "launch", "drag"])
def test_acting_actions_stay_gated(action: str):
    tool = tool_for(FakeManager())
    assert tool_is_read_only("desktop", tool, {"action": action}) is False
    assert _gate().assess("desktop", {"action": action}, tool).kind == "refuse"


def test_without_arguments_the_declared_tier_wins():
    """Call sites that judge the tool (not the call) keep the write tier."""
    tool = tool_for(FakeManager())
    assert tool_is_read_only("desktop", tool) is False


def _gate(mode: str = "plan") -> PermissionGate:
    """A gate with its default GuardPolicy (no workspace override needed)."""
    return PermissionGate(mode=mode, interactive=False)
