"""The host: one stdio loop, several resident projects, exact routing."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from zenagent.app.bootstrap import PreparedFrontend
from zenagent.app.runtime import RuntimeOptions
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
        resume = getattr(options, "resume", None)
        if resume is not None:
            # A resumed slot lands on the requested session, as a real prepare does.
            runtime.session = SimpleNamespace(path=Path(resume).resolve())
        opened[str(root)] = runtime
        return PreparedFrontend(
            runtime=runtime,
            broker=FakeBroker(),
            chat=FakeChat(),
            services=FakeServices(),
        )

    monkeypatch.setattr("zenagent.cli.rpc.host.prepare", fake_prepare)

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
    assert frame["protocol_version"] == 3
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

# -- several live sessions per project -------------------------------------


def _other_session(host_env) -> Path:
    return host_env.tmp_path / "alpha" / "sessions" / "other.jsonl"


async def _spawn_sibling(host, host_env) -> RuntimeSlot:
    """Make the first slot busy on its own session, then resume another one."""
    host_env.slot.chat.busy = True
    frame = await call(
        host,
        host_env.writes,
        {
            "type": "session",
            "op": "resume",
            "path": str(_other_session(host_env)),
            "id": "r",
        },
    )
    assert frame["ok"] is True
    return host._slots[f"{project_key(host_env.alpha)}#2"]


async def test_resuming_the_held_session_leaves_its_run_alone(host_env, monkeypatch):
    host = host_env.build()
    slot = host_env.slot
    slot.chat.busy = True
    touched: list[dict] = []

    async def fake_session(request, request_id):
        touched.append(request)

    monkeypatch.setattr(slot.server, "_session", fake_session)
    frame = await call(
        host,
        host_env.writes,
        {
            "type": "session",
            "op": "resume",
            "path": str(slot.runtime.session.path),
            "id": "r",
        },
    )
    assert frame["ok"] is True
    assert frame["path"] == str(slot.runtime.session.path)
    assert touched == []
    assert slot.chat.busy is True
    assert list(host._slots) == [project_key(host_env.alpha)]


async def test_resume_into_a_busy_slot_spawns_a_sibling_and_keeps_the_run(
    host_env,
):
    host = host_env.build()
    original = host_env.slot
    original.chat.busy = True

    frame = await call(
        host,
        host_env.writes,
        {
            "type": "session",
            "op": "resume",
            "path": str(_other_session(host_env)),
            "id": "r",
        },
    )
    key = project_key(host_env.alpha)
    assert frame["ok"] is True
    assert frame["path"] == str(_other_session(host_env).resolve())
    assert list(host._slots) == [key, f"{key}#2"]
    sibling = host._slots[f"{key}#2"]
    assert sibling.runtime.session.path == _other_session(host_env).resolve()
    assert original.runtime.closed is False
    assert original.chat.busy is True
    assert host.active is sibling
    assert host._active_by_project[key] is sibling


async def test_resume_of_a_held_session_activates_its_slot_without_switching(
    host_env, monkeypatch
):
    host = host_env.build()
    original = host_env.slot
    sibling = await _spawn_sibling(host, host_env)
    sibling.chat.busy = False
    touched: list[dict] = []

    async def fake_session(request, request_id):
        touched.append(request)

    monkeypatch.setattr(original.server, "_session", fake_session)
    monkeypatch.setattr(sibling.server, "_session", fake_session)
    frame = await call(
        host,
        host_env.writes,
        {
            "type": "session",
            "op": "resume",
            "path": str(original.runtime.session.path),
            "id": "r2",
        },
    )
    assert frame["ok"] is True
    assert touched == []
    assert host.active is original
    assert host._active_by_project[project_key(host_env.alpha)] is original
    assert len(host._slots) == 2


async def test_switching_back_to_a_running_session_activates_it_untouched(host_env):
    host = host_env.build()
    original = host_env.slot
    await _spawn_sibling(host, host_env)
    frame = await call(
        host,
        host_env.writes,
        {
            "type": "project",
            "op": "switch",
            "root": str(host_env.alpha),
            "resume": str(original.runtime.session.path),
            "id": "s",
        },
    )
    assert frame["key"] == project_key(host_env.alpha)
    assert host.active is original
    assert original.runtime.closed is False
    assert len(host._slots) == 2


async def test_resume_on_an_idle_slot_delegates_to_its_server(host_env, monkeypatch):
    host = host_env.build()
    slot = host_env.slot
    seen: list[dict] = []

    async def fake_session(request, request_id):
        seen.append(request)
        slot.server._send(
            {"type": "result", "id": request_id, "ok": True, "delegated": True}
        )

    monkeypatch.setattr(slot.server, "_session", fake_session)
    frame = await call(
        host,
        host_env.writes,
        {"type": "session", "op": "resume", "path": "/tmp/elsewhere.jsonl", "id": "r"},
    )
    assert frame["delegated"] is True
    assert seen[0]["path"] == "/tmp/elsewhere.jsonl"
    assert list(host._slots) == [project_key(host_env.alpha)]


async def test_resume_without_a_resident_slot_is_refused(host_env):
    host = RpcHost(
        initial=None,
        options=RuntimeOptions(root=host_env.alpha),
        read_line=None,
        write_line=_collect(host_env.writes),
    )
    frame = await call(
        host,
        host_env.writes,
        {"type": "session", "op": "resume", "path": "/tmp/x.jsonl", "id": "r"},
    )
    assert frame["type"] == "error"
    assert frame["code"] == "no_project"


async def test_resident_lists_each_live_session_with_its_slot_key(host_env):
    host = host_env.build()
    await _spawn_sibling(host, host_env)
    key = project_key(host_env.alpha)
    frame = await call(host, host_env.writes, {"type": "hello", "id": "h"})
    assert [item["key"] for item in frame["projects"]] == [key, f"{key}#2"]
    assert frame["projects"][1]["session"] == str(_other_session(host_env).resolve())


async def test_approval_ids_name_the_exact_slot_of_a_project(host_env):
    host = host_env.build()
    original = host_env.slot
    sibling = await _spawn_sibling(host, host_env)
    key = project_key(host_env.alpha)

    first = asyncio.create_task(original.server.confirm("bash", {}))
    second = asyncio.create_task(sibling.server.confirm("bash", {}))
    await asyncio.sleep(0)
    ids = [
        frame["event"]["request_id"]
        for frame in host_env.writes
        if frame.get("type") == "event"
        and frame["event"].get("type") == "approval_request"
    ]
    assert len(ids) == 2
    assert ids[0].startswith(f"{key}:a")
    assert ids[1].startswith(f"{key}#2:a")

    answered = await call(
        host,
        host_env.writes,
        {
            "type": "approval",
            "id": "a",
            "project": str(host_env.alpha),
            "request_id": ids[1],
            "decision": "allow",
        },
    )
    assert answered["ok"] is True
    assert await second == "allow"
    assert first.done() is False

    await call(
        host,
        host_env.writes,
        {
            "type": "approval",
            "id": "b",
            "project": str(host_env.alpha),
            "request_id": ids[0],
            "decision": "deny",
        },
    )
    assert await first == "deny"


async def test_closing_a_project_closes_every_one_of_its_slots(host_env):
    host = host_env.build()
    original = host_env.slot
    sibling = await _spawn_sibling(host, host_env)
    original.chat.busy = False
    sibling.chat.busy = False

    frame = await call(
        host,
        host_env.writes,
        {"type": "project", "op": "close", "root": str(host_env.alpha), "id": "c"},
    )
    assert frame["closed"] is True
    assert host._slots == {}
    assert host.active is None
    assert project_key(host_env.alpha) not in host._active_by_project
    assert original.runtime.closed is True
    assert sibling.runtime.closed is True
