"""Host-level supervisor status sees live slots."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from zenagent.app.agent_profile import AgentProfile
from zenagent.app.approval_router import ApprovalEntry
from zenagent.app.runtime import RuntimeOptions
from zenagent.app.workspace_state import set_workspace_mode
from zenagent.cli.rpc.host import RpcHost


class _Agent:
    def __init__(self):
        self.is_streaming = False
        self.aborted = False

    def abort(self):
        self.aborted = True


class _Runtime:
    def __init__(self):
        self.agent = _Agent()
        self.current_run_id = "r_1"
        self.run_scope = {}

    async def close(self):
        return None


class _Session:
    def __init__(self, path: Path):
        self.path = path


async def test_supervisor_status_lists_registered_agents(tmp_path):
    writes: list[str] = []
    host = RpcHost(
        initial=None,
        options=RuntimeOptions(root=tmp_path),
        read_line=lambda: None,
        write_line=writes.append,
    )
    supervisor = host._supervisor(tmp_path)
    runtime = _Runtime()
    supervisor.register(
        "slot-1",
        profile=AgentProfile(id="slot-1", kind="coding", label="slot-1"),
        runtime=runtime,
        session=_Session(tmp_path / "a.jsonl"),
    )

    await host._supervisor_op({"op": "status", "project": str(tmp_path)}, "s1")
    frame = json.loads(writes[-1])
    assert frame["type"] == "result"
    assert frame["ok"] is True
    assert [agent["key"] for agent in frame["agents"]] == ["slot-1"]

    await host._supervisor_op({"op": "explode", "project": str(tmp_path)}, "s2")
    assert json.loads(writes[-1])["code"] == "supervisor_invalid"


async def test_supervisor_approve_resolves_the_shared_router(tmp_path):
    writes: list[str] = []
    host = RpcHost(
        initial=None,
        options=RuntimeOptions(root=tmp_path),
        read_line=lambda: None,
        write_line=writes.append,
    )
    future = asyncio.get_running_loop().create_future()
    host._approvals.register(
        ApprovalEntry(request_id="slot:a1", project=str(tmp_path), future=future)
    )
    await host._supervisor_op(
        {"op": "approve", "requestId": "slot:a1", "decision": "allow"}, "a1"
    )
    assert await future == "allow"
    assert json.loads(writes[-1])["ok"] is True
    assert json.loads(writes[-1])["id"] == "a1"


async def test_supervisor_cancel_aborts_a_live_agent(tmp_path):
    writes: list[str] = []
    host = RpcHost(
        initial=None,
        options=RuntimeOptions(root=tmp_path),
        read_line=lambda: None,
        write_line=writes.append,
    )
    runtime = _Runtime()
    runtime.agent.is_streaming = True
    host._supervisor(tmp_path).register(
        "slot-1",
        profile=AgentProfile(id="slot-1"),
        runtime=runtime,
        session=_Session(tmp_path / "a.jsonl"),
    )
    await host._supervisor_op(
        {"op": "cancel", "project": str(tmp_path), "key": "slot-1"}, "c1"
    )
    assert runtime.agent.aborted is True
    assert json.loads(writes[-1])["cancelled"] is True


class _StubDispatcher:
    def __init__(self, mode: str, *, error: Exception | None = None) -> None:
        self.mode = mode
        self.error = error
        self.payloads: list[dict] = []

    async def dispatch(self, **payload):
        if self.error is not None:
            raise self.error
        self.payloads.append(payload)
        return {"dispatched": True, "status": "working"}


async def test_supervisor_dispatch_refuses_coding(tmp_path):
    writes: list[str] = []
    host = RpcHost(
        initial=None,
        options=RuntimeOptions(root=tmp_path),
        read_line=lambda: None,
        write_line=writes.append,
    )
    await host._supervisor_op(
        {"op": "dispatch", "project": str(tmp_path), "prompt": "x"}, "d1"
    )
    frame = json.loads(writes[-1])
    assert frame["code"] == "dispatch_not_orchestrated"


async def test_supervisor_dispatch_uses_workspace_mode_and_resolves_agent(tmp_path):
    writes: list[str] = []
    host = RpcHost(
        initial=None,
        options=RuntimeOptions(root=tmp_path),
        read_line=lambda: None,
        write_line=writes.append,
    )
    set_workspace_mode(tmp_path, "zen")
    dispatcher = _StubDispatcher("zen")
    host._supervisor(tmp_path).attach_dispatcher(dispatcher)
    session = _Session(tmp_path / "w1.jsonl")
    host._supervisor(tmp_path).register(
        "zen:w1",
        profile=AgentProfile(id="w1", kind="zen_worker"),
        runtime=_Runtime(),
        session=session,
    )

    await host._supervisor_op(
        {
            "op": "dispatch",
            "project": str(tmp_path),
            "prompt": "inspect",
            "agent": "zen:w1",
        },
        "d2",
    )
    frame = json.loads(writes[-1])
    assert frame["ok"] is True
    assert frame["mode"] == "zen"
    assert dispatcher.payloads == [{"prompt": "inspect", "session": str(session.path)}]


async def test_supervisor_dispatch_reports_missing_orchestrator(tmp_path):
    writes: list[str] = []
    host = RpcHost(
        initial=None,
        options=RuntimeOptions(root=tmp_path),
        read_line=lambda: None,
        write_line=writes.append,
    )
    set_workspace_mode(tmp_path, "zen")
    await host._supervisor_op(
        {
            "op": "dispatch",
            "project": str(tmp_path),
            "prompt": "x",
            "session": str(tmp_path / "w1.jsonl"),
        },
        "d3",
    )
    assert json.loads(writes[-1])["code"] == "dispatch_unavailable"


async def test_supervisor_dispatch_maps_strategy_refusals(tmp_path):
    writes: list[str] = []
    host = RpcHost(
        initial=None,
        options=RuntimeOptions(root=tmp_path),
        read_line=lambda: None,
        write_line=writes.append,
    )
    host._supervisor(tmp_path).attach_dispatcher(
        _StubDispatcher("swarm", error=ValueError("worker is working"))
    )
    await host._supervisor_op(
        {
            "op": "dispatch",
            "project": str(tmp_path),
            "mode": "swarm",
            "prompt": "x",
        },
        "d4",
    )
    frame = json.loads(writes[-1])
    assert frame["code"] == "dispatch_rejected"
    assert "worker is working" in frame["message"]


async def test_supervisor_dispatch_forwards_swarm_quote_and_images(tmp_path):
    writes: list[str] = []
    host = RpcHost(
        initial=None,
        options=RuntimeOptions(root=tmp_path),
        read_line=lambda: None,
        write_line=writes.append,
    )
    dispatcher = _StubDispatcher("swarm")
    host._supervisor(tmp_path).attach_dispatcher(dispatcher)
    await host._supervisor_op(
        {
            "op": "dispatch",
            "project": str(tmp_path),
            "mode": "swarm",
            "prompt": "look",
            "ref": "m1",
            "images": ["/tmp/a.png"],
        },
        "d5",
    )
    assert dispatcher.payloads == [
        {"prompt": "look", "ref": "m1", "images": ["/tmp/a.png"]}
    ]
    assert json.loads(writes[-1])["mode"] == "swarm"


async def test_supervisor_dispatch_validates_swarm_images(tmp_path):
    writes: list[str] = []
    host = RpcHost(
        initial=None,
        options=RuntimeOptions(root=tmp_path),
        read_line=lambda: None,
        write_line=writes.append,
    )
    await host._supervisor_op(
        {
            "op": "dispatch",
            "project": str(tmp_path),
            "mode": "swarm",
            "prompt": "look",
            "images": "not-a-list",
        },
        "d6",
    )
    assert json.loads(writes[-1])["code"] == "supervisor_invalid"
