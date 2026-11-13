"""Backend workspace mode: storage and the host RPC surface."""

from __future__ import annotations

import json
from pathlib import Path

from zenagent.app.runtime import RuntimeOptions
from zenagent.app.workspace_state import (
    WorkspaceState,
    load_workspace,
    save_workspace,
    workspace_path,
)
from zenagent.cli.rpc.host import RpcHost


def _host(tmp_path: Path, writes: list[str]) -> RpcHost:
    return RpcHost(
        initial=None,
        options=RuntimeOptions(root=tmp_path),
        read_line=lambda: None,
        write_line=writes.append,
    )


def test_missing_or_corrupt_state_falls_back_to_coding(tmp_path):
    assert load_workspace(tmp_path) == WorkspaceState()
    path = workspace_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("{bad", encoding="utf-8")
    assert load_workspace(tmp_path).mode == "coding"


def test_save_round_trip(tmp_path):
    state = save_workspace(
        tmp_path, WorkspaceState(mode="zen", router_session="/p/router.jsonl")
    )
    assert state.mode == "zen"
    assert state.updated_at > 0
    loaded = load_workspace(tmp_path)
    assert loaded.mode == "zen"
    assert loaded.router_session == "/p/router.jsonl"


async def test_workspace_rpc_get_and_set(tmp_path):
    writes: list[str] = []
    host = _host(tmp_path, writes)

    await host._workspace_op({"op": "get", "project": str(tmp_path)}, "w1")
    first = json.loads(writes[-1])
    assert first["mode"] == "coding"

    await host._workspace_op(
        {"op": "set", "project": str(tmp_path), "mode": "zen"}, "w2"
    )
    frame = json.loads(writes[-1])
    assert frame["ok"] is True
    assert frame["mode"] == "zen"
    broadcast = json.loads(writes[-2])
    assert broadcast["type"] == "workspace"
    assert broadcast["mode"] == "zen"
    assert load_workspace(tmp_path).mode == "zen"

    await host._workspace_op(
        {"op": "set", "project": str(tmp_path), "mode": "banana"}, "w3"
    )
    assert json.loads(writes[-1])["code"] == "workspace_invalid"
