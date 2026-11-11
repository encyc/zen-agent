"""The router's fleet tools: dispatch/status surface for the zen fleet."""

from __future__ import annotations

import json
from pathlib import Path

from zenagent.app.zen.tools import fleet_tools


class FakeFleet:
    router_path = "/p/sessions/router.jsonl"

    def __init__(self) -> None:
        self.sessions_dir = Path("/p/sessions")
        self.dispatched: list[tuple[str, str]] = []
        self.stopped: list[str] = []
        self.focused: list[tuple[str, str]] = []
        self.spawned: list[str] = []
        self.roster_rows: list[dict] = [
            {
                "path": "/p/sessions/router.jsonl",
                "title": "router",
                "focus": "",
                "role": "router",
                "status": "idle",
                "last_report": "",
            },
            {
                "path": "/p/sessions/w1.jsonl",
                "title": "w1",
                "focus": "编译修复",
                "role": "worker",
                "status": "waiting",
                "last_report": "done",
            },
        ]

    def roster(self) -> list[dict]:
        return self.roster_rows

    async def dispatch(self, session: str, task: str) -> str:
        if session == self.router_path:
            raise ValueError("cannot dispatch the router session")
        self.dispatched.append((session, task))
        return "working"

    async def stop(self, session_path: str) -> bool:
        self.stopped.append(session_path)
        return True

    def focus(self, session_path: str, focus: str) -> None:
        self.focused.append((session_path, focus))

    def spawn(self, focus: str) -> str:
        path = f"/p/sessions/spawn-{len(self.spawned)}.jsonl"
        self.spawned.append(focus)
        return path

    def status_of(self, session_path: str) -> str:
        return "waiting"


async def _run(tool, args: dict):
    return await tool.execute("call-1", args, None, None)


def _text(result) -> str:
    return "".join(getattr(block, "text", "") for block in result.content)


def test_six_fleet_tools_exposed() -> None:
    names = {tool.name for tool in fleet_tools(lambda: FakeFleet())}
    assert {
        "fleet_list",
        "fleet_dispatch",
        "fleet_status",
        "fleet_stop",
        "fleet_focus",
        "fleet_spawn",
    } == names


async def test_list_renders_roster_json() -> None:
    tools = {tool.name: tool for tool in fleet_tools(lambda: FakeFleet())}
    result = await _run(tools["fleet_list"], {})
    rows = json.loads(_text(result))
    assert rows[0]["role"] == "router"
    assert rows[1]["focus"] == "编译修复"


async def test_dispatch_resolves_bare_stem_to_sessions_path() -> None:
    fleet = FakeFleet()
    tools = {tool.name: tool for tool in fleet_tools(lambda: fleet)}
    result = await _run(
        tools["fleet_dispatch"], {"session": "w1", "task": "修复编译警告"}
    )
    assert "working" in _text(result)
    assert fleet.dispatched[0][0].endswith("w1.jsonl")


async def test_dispatch_refuses_router_session() -> None:
    tools = {tool.name: tool for tool in fleet_tools(lambda: FakeFleet())}
    result = await _run(
        tools["fleet_dispatch"],
        {"session": "/p/sessions/router.jsonl", "task": "x"},
    )
    assert result.is_error


async def test_status_reports_single_worker() -> None:
    tools = {tool.name: tool for tool in fleet_tools(lambda: FakeFleet())}
    result = await _run(tools["fleet_status"], {"session": "/p/sessions/w1.jsonl"})
    assert "waiting" in _text(result)


async def test_stop_and_focus_and_spawn_pass_through() -> None:
    fleet = FakeFleet()
    tools = {tool.name: tool for tool in fleet_tools(lambda: fleet)}
    await _run(tools["fleet_stop"], {"session": "/p/sessions/w1.jsonl"})
    await _run(tools["fleet_focus"], {"session": "/p/sessions/w1.jsonl", "focus": "性能巡检"})
    result = await _run(tools["fleet_spawn"], {"focus": "冒烟测试"})
    assert fleet.stopped == ["/p/sessions/w1.jsonl"]
    assert fleet.focused == [("/p/sessions/w1.jsonl", "性能巡检")]
    assert "spawn-0" in _text(result)


async def test_tools_degrade_without_fleet() -> None:
    tools = {tool.name: tool for tool in fleet_tools(lambda: None)}
    for name, args in (
        ("fleet_list", {}),
        ("fleet_dispatch", {"session": "w1", "task": "x"}),
        ("fleet_status", {"session": "w1"}),
        ("fleet_stop", {"session": "w1"}),
        ("fleet_focus", {"session": "w1", "focus": "f"}),
        ("fleet_spawn", {"focus": "f"}),
    ):
        result = await _run(tools[name], args)
        assert "zen" in _text(result).lower()
