"""Host-level supervisor status sees live slots."""

from __future__ import annotations

import json
from pathlib import Path

from zenagent.app.agent_profile import AgentProfile
from zenagent.app.runtime import RuntimeOptions
from zenagent.cli.rpc.host import RpcHost


class _Agent:
    is_streaming = False


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
    supervisor.register(
        "slot-1",
        profile=AgentProfile(id="slot-1", kind="coding", label="slot-1"),
        runtime=_Runtime(),
        session=_Session(tmp_path / "a.jsonl"),
    )

    await host._supervisor_op({"op": "status", "project": str(tmp_path)}, "s1")
    frame = json.loads(writes[-1])
    assert frame["type"] == "result"
    assert frame["ok"] is True
    assert [agent["key"] for agent in frame["agents"]] == ["slot-1"]

    await host._supervisor_op({"op": "explode", "project": str(tmp_path)}, "s2")
    assert json.loads(writes[-1])["code"] == "supervisor_invalid"
