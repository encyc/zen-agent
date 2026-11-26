# tests/test_automations_rpc.py
"""The ``automations`` wire domain: cross-project listing, no Runtime built.

These ops exist so the desktop can manage automations in projects the user has
not opened. That is why several tests also check the design's weaker claim:
nothing here constructs a Runtime or materialises a transcript.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from zenagent.app.bootstrap import PreparedFrontend
from zenagent.app.runtime import RuntimeOptions
from zenagent.cli.rpc.host import RpcHost
from zenagent.cli.rpc.slot import RuntimeSlot


class _Agent:
    is_streaming = False
    termination = "stop"

    def __init__(self) -> None:
        self.messages: list = []
        self.tools: list = []

    def subscribe(self, listener) -> None:
        pass

    def abort(self) -> None:
        pass


class _Runtime:
    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.cwd = self.root
        self.agent = _Agent()
        self.session = SimpleNamespace(path=self.root / "sessions" / "s.jsonl")
        self.ask_handler = None
        self.mode = "ask"
        self.warnings: list[str] = []

    async def close(self) -> None:
        pass


class _Chat:
    busy = False

    def reload_transcript(self) -> None:
        pass

    async def wait_for_idle(self) -> None:
        pass


class _Broker:
    def set_confirm(self, fn) -> None:
        pass


@pytest.fixture
def rpc(tmp_path, monkeypatch):
    """An ``RpcHost`` with one resident slot; it records the projects it opens."""
    opened: dict[str, _Runtime] = {}

    async def fake_prepare(options, **kwargs):
        root = Path(options.root).resolve()
        runtime = _Runtime(root)
        opened[str(root)] = runtime
        return PreparedFrontend(
            runtime=runtime,
            broker=_Broker(),
            chat=_Chat(),
            services=SimpleNamespace(models=None, sessions=None),
        )

    monkeypatch.setattr("zenagent.cli.rpc.host.prepare", fake_prepare)

    alpha = tmp_path / "alpha"
    (alpha / ".git").mkdir(parents=True)
    return SimpleNamespace(
        tmp_path=tmp_path, opened=opened, writes=[], prepare=fake_prepare
    )


async def _host(env) -> RpcHost:
    alpha = env.tmp_path / "alpha"
    prepared = await env.prepare(RuntimeOptions(root=alpha))

    def write(line: str) -> None:
        env.writes.append(json.loads(line))

    slot = RuntimeSlot.adopt(prepared, write_line=write)
    return RpcHost(
        initial=slot,
        options=RuntimeOptions(root=alpha),
        read_line=None,
        write_line=write,
        max_slots=4,
    )


async def call(host: RpcHost, writes: list[dict], request: dict) -> dict:
    """Send one frame and return the reply carrying its id.

    The host also emits unsolicited frames (slot status, project events), so
    "the last thing written" is not reliably the answer to this request.
    """
    before = len(writes)
    assert await host._handle_line(json.dumps(request))
    for frame in writes[before:]:
        if frame.get("id") == request.get("id") and frame.get("type") in (
            "result",
            "error",
        ):
            return frame
    raise AssertionError(
        f"no reply for {request.get('id')!r}; got {[f.get('id') for f in writes[before:]]}"
    )


def _repo(tmp_path, name):
    root = tmp_path / name
    (root / ".git").mkdir(parents=True)
    return root


async def test_add_then_list_all_across_projects(rpc):
    a = _repo(rpc.tmp_path, "aut-a")
    b = _repo(rpc.tmp_path, "aut-b")
    host = await _host(rpc)
    await call(
        host,
        rpc.writes,
        {
            "type": "automations",
            "op": "add",
            "id": "1",
            "project": str(a),
            "name": "r",
            "prompt": "go",
            "schedule": "daily 09:00",
        },
    )
    await call(
        host,
        rpc.writes,
        {
            "type": "automations",
            "op": "add",
            "id": "2",
            "project": str(b),
            "name": "s",
            "prompt": "go",
            "schedule": "every 15m",
        },
    )
    reply = await call(
        host, rpc.writes, {"type": "automations", "op": "list_all", "id": "3"}
    )
    names = sorted(row["name"] for row in reply["jobs"])
    assert names == ["r", "s"]


async def test_add_does_not_construct_a_runtime_or_create_a_session_file(rpc):
    root = _repo(rpc.tmp_path, "aut-c")
    host = await _host(rpc)
    await call(
        host,
        rpc.writes,
        {
            "type": "automations",
            "op": "add",
            "id": "1",
            "project": str(root),
            "name": "r",
            "prompt": "go",
            "schedule": "daily 09:00",
        },
    )
    sessions = root / "sessions"
    assert not sessions.exists() or list(sessions.glob("*.jsonl")) == []
    assert str(root.resolve()) not in rpc.opened


async def test_update_toggles_and_rm_removes(rpc):
    root = _repo(rpc.tmp_path, "aut-d")
    host = await _host(rpc)
    base = {"type": "automations", "project": str(root), "name": "r"}
    await call(
        host,
        rpc.writes,
        {**base, "op": "add", "id": "1", "prompt": "go", "schedule": "daily 09:00"},
    )
    await call(host, rpc.writes, {**base, "op": "update", "id": "2", "enabled": False})
    reply = await call(host, rpc.writes, {**base, "op": "list", "id": "3"})
    assert reply["jobs"][0]["enabled"] is False
    await call(host, rpc.writes, {**base, "op": "rm", "id": "4"})
    reply = await call(host, rpc.writes, {**base, "op": "list", "id": "5"})
    assert reply["jobs"] == []


async def test_bad_schedule_is_refused_not_a_crash(rpc):
    root = _repo(rpc.tmp_path, "aut-e")
    host = await _host(rpc)
    reply = await call(
        host,
        rpc.writes,
        {
            "type": "automations",
            "op": "add",
            "id": "1",
            "project": str(root),
            "name": "r",
            "prompt": "go",
            "schedule": "hourly",
        },
    )
    assert reply.get("ok") is False or reply.get("type") == "error"
