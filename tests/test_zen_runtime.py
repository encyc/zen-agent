"""Runtime wiring for zen mode: fleet slot, fleet tools, router prompt note."""

from __future__ import annotations

from zenagent.app.runtime import Runtime  # noqa: F401 - fixture surface contract
from zenagent.app.zen.fleet import ZenFleet
from zenagent.app.zen.prompts import ROUTER_PROMPT_NOTE
from zenagent.app.zen.tools import register_router_surface
from zenagent.project import project_sessions_dir


async def test_runtime_has_a_fleet_slot_defaulting_to_none(runtime_factory):
    runtime = await runtime_factory()
    assert runtime.zen_fleet is None


async def test_fleet_tools_and_router_note_join_when_fleet_is_set(runtime_factory):
    runtime = await runtime_factory()
    base = {tool.name for tool in runtime.agent.tools}
    assert not any(name.startswith("fleet_") for name in base)
    assert "Router 纪律" not in runtime.agent.system_prompt

    fleet = ZenFleet(
        chat=None,
        root=runtime.root,
        sessions_dir=project_sessions_dir(runtime.root),
        router_path=str(runtime.session.path),
    )
    runtime.zen_fleet = fleet
    register_router_surface(runtime)
    runtime._recompose()
    try:
        names = {tool.name for tool in runtime.agent.tools}
        assert {
            "fleet_list",
            "fleet_dispatch",
            "fleet_status",
            "fleet_stop",
            "fleet_focus",
            "fleet_spawn",
        } <= names
        assert "Router 纪律" in runtime.agent.system_prompt
    finally:
        runtime.zen_fleet = None
        runtime._recompose()

    assert "fleet_dispatch" not in {tool.name for tool in runtime.agent.tools}
    assert "Router 纪律" not in runtime.agent.system_prompt


async def test_router_note_mentions_async_result_delivery(runtime_factory):
    runtime = await runtime_factory()
    fleet = ZenFleet(
        chat=None,
        root=runtime.root,
        sessions_dir=project_sessions_dir(runtime.root),
        router_path=str(runtime.session.path),
    )
    runtime.zen_fleet = fleet
    register_router_surface(runtime)
    runtime._recompose()
    try:
        assert "禁止" in runtime.agent.system_prompt
        assert ROUTER_PROMPT_NOTE.strip() in runtime.agent.system_prompt
    finally:
        runtime.zen_fleet = None
        runtime._recompose()


async def test_fleet_tools_follow_the_router_session_not_the_slot(runtime_factory):
    """A session switch in the same slot must not inherit the router surface."""
    runtime = await runtime_factory()
    fleet = ZenFleet(
        chat=None,
        root=runtime.root,
        sessions_dir=project_sessions_dir(runtime.root),
        router_path=str(runtime.session.path),
    )
    runtime.zen_fleet = fleet
    register_router_surface(runtime)
    runtime._recompose()
    try:
        names = {tool.name for tool in runtime.agent.tools}
        assert "fleet_list" in names
        assert "Router 纪律" in runtime.agent.system_prompt

        await runtime.switch_session(None)

        names = {tool.name for tool in runtime.agent.tools}
        assert not any(name.startswith("fleet_") for name in names)
        assert "Router 纪律" not in runtime.agent.system_prompt
    finally:
        runtime.zen_fleet = None
        runtime._recompose()
