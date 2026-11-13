"""Typed approval correlation shared by slots and swarm rooms."""

from __future__ import annotations

import asyncio
import json

from zenagent.app.approval_router import ApprovalEntry, ApprovalRouter
from zenagent.app.runtime import RuntimeOptions
from zenagent.cli.rpc.host import RpcHost


async def test_router_resolves_pending_entry():
    router = ApprovalRouter()
    future = asyncio.get_running_loop().create_future()
    router.register(
        ApprovalEntry(
            request_id="p:swarm:a1",
            project="/repo",
            agent_id="research",
            run_id="r1",
            future=future,
        )
    )
    assert router.pending()[0]["requestId"] == "p:swarm:a1"
    assert router.resolve("p:swarm:a1", "allow") is True
    assert await future == "allow"
    assert router.resolve("p:swarm:a1", "allow") is False
    router.forget("p:swarm:a1")
    assert router.pending() == []


async def test_router_clear_denies_pending():
    router = ApprovalRouter()
    future = asyncio.get_running_loop().create_future()
    router.register(ApprovalEntry(request_id="a", future=future))
    router.clear()
    assert await future == "deny"
    assert router.pending() == []


async def test_host_routes_an_approval_by_lookup(tmp_path):
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

    assert await host._handle_line(
        json.dumps(
            {"type": "approval", "id": "x", "request_id": "slot:a1", "decision": "deny"}
        )
    )
    assert await future == "deny"
    assert json.loads(writes[-1]) == {"type": "result", "id": "x", "ok": True}
