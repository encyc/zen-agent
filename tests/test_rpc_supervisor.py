"""Host-level supervisor status sees live slots."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from zenagent.app.agent_profile import AgentProfile
from zenagent.app.approval_router import ApprovalEntry
from zenagent.app.runtime import RuntimeOptions
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
