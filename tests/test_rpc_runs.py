"""The host-level `runs` query family."""

from __future__ import annotations

import json
from pathlib import Path

from zenagent.app.runs import RunLedger
from zenagent.app.runtime import RuntimeOptions
from zenagent.cli.rpc.host import RpcHost
from zenagent.project import project_dir


def _host(tmp_path: Path, writes: list[str]) -> RpcHost:
    return RpcHost(
        initial=None,
        options=RuntimeOptions(root=tmp_path),
        read_line=lambda: None,
        write_line=writes.append,
    )


def _seed(tmp_path: Path) -> tuple[str, str]:
    ledger = RunLedger(project_dir(root=tmp_path) / "runs.jsonl")
    coding = ledger.start(kind="coding", agent_id="main", session="/p/a.jsonl")
    ledger.finish(coding, status="done", termination="stop", turns=1, cost=0.1)
    swarm = ledger.start(
        kind="swarm",
        agent_id="research",
        session="/p/b.jsonl",
        work_item_id="w1",
        task_id="t1",
    )
    ledger.finish(swarm, status="failed", termination="max_turns")
    return coding.id, swarm.id


async def test_runs_list_filters_and_projects(tmp_path):
    coding_id, swarm_id = _seed(tmp_path)
    writes: list[str] = []
    host = _host(tmp_path, writes)

    await host._runs_op({"op": "list", "project": str(tmp_path)}, "r1")
    frame = json.loads(writes[-1])
    assert frame["type"] == "result"
    assert frame["ok"] is True
    assert frame["total"] == 2
    assert {row["id"] for row in frame["runs"]} == {coding_id, swarm_id}
    assert "type" not in frame["runs"][0]
    assert "version" not in frame["runs"][0]

    await host._runs_op(
        {"op": "list", "project": str(tmp_path), "kind": "swarm"}, "r2"
    )
    frame = json.loads(writes[-1])
    assert [row["id"] for row in frame["runs"]] == [swarm_id]
    assert frame["runs"][0]["workItemId"] == "w1"
    assert frame["runs"][0]["taskId"] == "t1"


async def test_runs_get_and_unknown_op(tmp_path):
    coding_id, _ = _seed(tmp_path)
    writes: list[str] = []
    host = _host(tmp_path, writes)

    await host._runs_op(
        {"op": "get", "project": str(tmp_path), "runId": coding_id}, "g1"
    )
    frame = json.loads(writes[-1])
    assert frame["run"]["id"] == coding_id
    assert frame["run"]["status"] == "done"

    await host._runs_op({"op": "get", "project": str(tmp_path), "runId": "nope"}, "g2")
    assert json.loads(writes[-1])["run"] is None

    await host._runs_op({"op": "explode", "project": str(tmp_path)}, "g3")
    frame = json.loads(writes[-1])
    assert frame["type"] == "error"
    assert frame["code"] == "runs_invalid"
