"""The host: one stdio loop, several resident projects, exact routing."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from zenagent.app.bootstrap import PreparedFrontend
from zenagent.app.runtime import RuntimeOptions
from zenagent.app.taiji import TaijiClaim
from zenagent.cli.rpc.host import RpcHost
from zenagent.cli.rpc.slot import RuntimeSlot
from zenagent.project import project_key


class FakeAgent:
    def __init__(self, name: str) -> None:
        self.messages: list = []
        self.is_streaming = False
        self.termination = "stop"
        self.tools = [
            SimpleNamespace(name=f"{name}-tool", description="", approval="read")
        ]

    def subscribe(self, listener) -> None:
        self._listener = listener

    def abort(self) -> None:
        pass


class FakeChat:
    def __init__(self) -> None:
        self.busy = False

    def reload_transcript(self) -> None:
        pass

    async def wait_for_idle(self) -> None:
        pass


class FakeRuntime:
    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.cwd = self.root
        self.agent = FakeAgent(self.root.name)
        self.session = SimpleNamespace(path=self.root / "sessions" / "s.jsonl")
        self.ask_handler = None
        self.taiji_chat = None
        self.mode = "ask"
        self.warnings: list[str] = []
        self.closed = False

    def _note(self, text: str) -> None:
        pass

    async def close(self) -> None:
        self.closed = True


class FakeBroker:
    def set_confirm(self, fn) -> None:
        self.confirm = fn


class FakeServices:
    models = None
    sessions = None


def _collect(writes: list[dict]):
    def write(line: str) -> None:
        writes.append(json.loads(line))

    return write


def _repo(tmp_path: Path, name: str) -> Path:
    root = tmp_path / name
    (root / ".git").mkdir(parents=True)
    return root


@pytest.fixture
async def host_env(tmp_path, monkeypatch):
    opened: dict[str, FakeRuntime] = {}

    async def fake_prepare(options, **kwargs):
        root = Path(options.root).resolve()
        runtime = FakeRuntime(root)
        opened[str(root)] = runtime
        return PreparedFrontend(
            runtime=runtime,
            broker=FakeBroker(),
            chat=FakeChat(),
            services=FakeServices(),
        )

    monkeypatch.setattr("zenagent.cli.rpc.host.prepare", fake_prepare)
    monkeypatch.setattr(
        "zenagent.cli.rpc.slot.claim_taiji_host",
        lambda runtime: TaijiClaim(root=runtime.root, owned=False),
    )

    async def fake_release(claim) -> None:
        return None

    monkeypatch.setattr("zenagent.cli.rpc.slot.release_taiji_host", fake_release)

    alpha = _repo(tmp_path, "alpha")
    beta = _repo(tmp_path, "beta")
    gamma = _repo(tmp_path, "gamma")
    writes: list[dict] = []
    prepared = await fake_prepare(RuntimeOptions(root=alpha))
    slot = RuntimeSlot.adopt(prepared, write_line=_collect(writes))

    def build(max_slots: int = 4) -> RpcHost:
        return RpcHost(
            initial=slot,
            options=RuntimeOptions(root=alpha),
            read_line=None,
            write_line=_collect(writes),
            max_slots=max_slots,
        )

    return SimpleNamespace(
        alpha=alpha,
        beta=beta,
        gamma=gamma,
        tmp_path=tmp_path,
        writes=writes,
        build=build,
        opened=opened,
        slot=slot,
    )


async def call(host: RpcHost, writes: list[dict], request: dict) -> dict:
    assert await host._handle_line(json.dumps(request))
    return writes[-1]


async def test_hello_lists_the_active_project_and_residents(host_env):
    host = host_env.build()
    frame = await call(host, host_env.writes, {"type": "hello", "id": "h"})
    assert frame["protocol_version"] == 2
    assert frame["active"] == str(host_env.alpha.resolve())
    assert [item["key"] for item in frame["projects"]] == [
        project_key(host_env.alpha)
    ]


async def test_open_switches_active_and_by_default_routes_there(host_env):
    host = host_env.build()
    opened = await call(
        host,
        host_env.writes,
        {"type": "project", "op": "open", "root": str(host_env.beta), "id": "o"},
    )
    assert opened["ok"] is True
    assert opened["active"] == str(host_env.beta.resolve())
    assert len(opened["resident"]) == 2

    requested = await call(host, host_env.writes, {"type": "tools", "id": "t"})
    assert [tool["name"] for tool in requested["tools"]] == ["beta-tool"]


async def test_requests_route_by_the_project_field(host_env):
    host = host_env.build()
    await call(
        host,
        host_env.writes,
        {"type": "project", "op": "open", "root": str(host_env.beta), "id": "o"},
    )
    requested = await call(
        host,
        host_env.writes,
        {"type": "tools", "id": "t", "project": str(host_env.alpha)},
    )
    assert [tool["name"] for tool in requested["tools"]] == ["alpha-tool"]
    assert requested["project"] == str(host_env.alpha.resolve())


async def test_a_named_project_that_is_not_resident_is_never_substituted(host_env):
    host = host_env.build()
    frame = await call(
        host,
        host_env.writes,
        {"type": "tools", "id": "t", "project": str(host_env.beta)},
    )
    assert frame["type"] == "error"
    assert frame["code"] == "no_project"


async def test_close_falls_back_to_the_most_recent_resident(host_env):
    host = host_env.build()
    await call(
        host,
        host_env.writes,
        {"type": "project", "op": "open", "root": str(host_env.beta), "id": "o"},
    )
    closed = await call(
        host,
        host_env.writes,
        {"type": "project", "op": "close", "root": str(host_env.beta), "id": "c"},
    )
    assert closed["closed"] is True
    assert closed["resident"] == [
        {
            "key": project_key(host_env.alpha),
            "root": str(host_env.alpha.resolve()),
            "session": str(host_env.alpha.resolve() / "sessions" / "s.jsonl"),
        }
    ]
    assert host.active.key == project_key(host_env.alpha)


async def test_lru_eviction_drops_the_oldest_idle_slot(host_env):
    host = host_env.build(max_slots=2)
    await call(
        host,
        host_env.writes,
        {"type": "project", "op": "open", "root": str(host_env.beta), "id": "b"},
    )
    alpha_runtime = host_env.opened[str(host_env.alpha.resolve())]
    await call(
        host,
        host_env.writes,
        {"type": "project", "op": "open", "root": str(host_env.gamma), "id": "g"},
    )
    assert list(host._slots) == [
        project_key(host_env.beta),
        project_key(host_env.gamma),
    ]
    assert alpha_runtime.closed is True


async def test_eviction_refuses_when_every_slot_is_pinned(host_env):
    host = host_env.build(max_slots=2)
    await call(
        host,
        host_env.writes,
        {"type": "project", "op": "open", "root": str(host_env.beta), "id": "b"},
    )
    host_env.slot.chat.busy = True
    host._slots[project_key(host_env.beta)].chat.busy = True
    frame = await call(
        host,
        host_env.writes,
        {"type": "project", "op": "open", "root": str(host_env.gamma), "id": "g"},
    )
    assert frame["type"] == "error"
    assert frame["code"] == "project_busy"
    assert len(host._slots) == 2


async def test_relocate_refuses_a_resident_project_and_delegates_otherwise(
    host_env, monkeypatch
):
    host = host_env.build()
    await call(
        host,
        host_env.writes,
        {"type": "project", "op": "open", "root": str(host_env.beta), "id": "b"},
    )
    refused = await call(
        host,
        host_env.writes,
        {
            "type": "project",
            "op": "relocate",
            "from": str(host_env.alpha),
            "to": str(host_env.tmp_path / "alpha2"),
            "id": "r1",
        },
    )
    assert refused["code"] == "project_busy"

    await call(
        host,
        host_env.writes,
        {"type": "project", "op": "close", "root": str(host_env.alpha), "id": "c"},
    )
    beta_slot = host._slots[project_key(host_env.beta)]
    calls: list[dict] = []

    async def fake_project(request, request_id):
        calls.append(request)
        beta_slot.server._send(
            {"type": "result", "id": request_id, "ok": True, "delegated": True}
        )

    monkeypatch.setattr(beta_slot.server, "_project", fake_project)
    delegated = await call(
        host,
        host_env.writes,
        {
            "type": "project",
            "op": "relocate",
            "from": str(host_env.alpha),
            "to": str(host_env.tmp_path / "alpha2"),
            "id": "r2",
        },
    )
    assert delegated["delegated"] is True
    assert calls[0]["from"] == str(host_env.alpha)


async def test_approval_routes_by_its_prefixed_id_even_when_project_names_beta(
    host_env,
):
    host = host_env.build()
    await call(
        host,
        host_env.writes,
        {"type": "project", "op": "open", "root": str(host_env.beta), "id": "b"},
    )
    alpha_slot = host._slots[project_key(host_env.alpha)]
    pending = asyncio.create_task(alpha_slot.server.confirm("bash", {}))
    await asyncio.sleep(0)
    request_id = host_env.writes[-1]["event"]["request_id"]
    assert request_id.startswith(f"{project_key(host_env.alpha)}:a")

    frame = await call(
        host,
        host_env.writes,
        {
            "type": "approval",
            "id": "a",
            "project": str(host_env.beta),
            "request_id": request_id,
            "decision": "allow",
        },
    )
    assert frame["ok"] is True
    assert await pending == "allow"