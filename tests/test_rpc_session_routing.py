"""Session-tagged routing: a request naming a session path is served by the
resident slot holding it, and never silently by the active slot.

The desktop's multi-session panes address background sessions directly
(prompt/status/transcript carry a ``session`` field); these tests pin the
routing rule and its refusal for non-resident sessions."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from zenagent.app.bootstrap import PreparedFrontend
from zenagent.app.runtime import RuntimeOptions
from zenagent.app.session_lease import SessionLeaseBusy
from zenagent.cli.rpc.host import RpcHost
from zenagent.cli.rpc.slot import RuntimeSlot
from zenagent.project import project_key


class FakeAgent:
    def __init__(self, name: str) -> None:
        self.messages: list = []
        self.is_streaming = False
        self.termination = "stop"
        self.tools: list = []
        self.model = SimpleNamespace(id="m")

    def subscribe(self, listener) -> None:
        self._listener = listener

    def abort(self) -> None:
        pass


class FakeChat:
    def __init__(self) -> None:
        self.busy = False
        self.transcript: list = []
        self.notices_snapshot: list = []

    def reload_transcript(self, drop_pending: bool = False) -> None:
        pass

    async def wait_for_idle(self) -> None:
        pass


class FakeRuntime:
    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.cwd = self.root
        self.agent = FakeAgent(self.root.name)
        self.session = SimpleNamespace(
            path=self.root / "sessions" / "s.jsonl",
            ensure_loaded=lambda: None,
            records=[],
        )
        self.ask_handler = None
        self.mode = "ask"
        self.warnings: list[str] = []
        self.closed = False

    def _note(self, text: str) -> None:
        pass

    async def switch_session(self, path, *, carry_todos: bool = False) -> None:
        self.session = SimpleNamespace(
            path=Path(path).resolve(), ensure_loaded=lambda: None, records=[]
        )

    async def close(self) -> None:
        self.closed = True


class StubSessions:
    # `new` mirrors the kernel's lease rule: busy refuses, idle switches.
    def __init__(self, runtime: FakeRuntime, is_busy) -> None:
        self._runtime = runtime
        self._is_busy = is_busy
        self._seq = 0

    async def new(self):
        if self._is_busy():
            raise SessionLeaseBusy()
        self._seq += 1
        path = self._runtime.root / "sessions" / f"stub-new{self._seq}.jsonl"
        self._runtime.session = SimpleNamespace(
            path=path, ensure_loaded=lambda: None, records=[]
        )
        return path


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
    fresh_seq = [0]

    async def fake_prepare(options, **kwargs):
        root = Path(options.root).resolve()
        runtime = FakeRuntime(root)
        chat = FakeChat()
        resume = getattr(options, "resume", None)
        if resume is not None:
            runtime.session = SimpleNamespace(
                path=Path(resume).resolve(),
                ensure_loaded=lambda: None,
                records=[],
            )
        else:
            # Fresh sessions get unique names so sibling slots never share a
            # session path (the holder scan routes by exact path).
            fresh_seq[0] += 1
            runtime.session = SimpleNamespace(
                path=root / "sessions" / f"fresh{fresh_seq[0]}.jsonl",
                ensure_loaded=lambda: None,
                records=[],
            )
        services = FakeServices()
        services.sessions = StubSessions(runtime, lambda: chat.busy)
        return PreparedFrontend(
            runtime=runtime,
            broker=FakeBroker(),
            chat=chat,
            services=services,
        )

    monkeypatch.setattr("zenagent.cli.rpc.host.prepare", fake_prepare)

    alpha = _repo(tmp_path, "alpha")
    beta = _repo(tmp_path, "beta")
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
        tmp_path=tmp_path,
        writes=writes,
        build=build,
        slot=slot,
    )


async def call(host: RpcHost, writes: list[dict], request: dict) -> dict:
    assert await host._handle_line(json.dumps(request))
    return writes[-1]


def _other_session(host_env) -> Path:
    return host_env.tmp_path / "alpha" / "sessions" / "other.jsonl"


async def _spawn_sibling(host, host_env) -> RuntimeSlot:
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


async def _switch_back(host, host_env) -> None:
    """Resume the original session: a no-op server-side, but it re-activates
    the first slot — the sibling becomes a background session, the pane case."""
    frame = await call(
        host,
        host_env.writes,
        {
            "type": "session",
            "op": "resume",
            "path": str(host_env.slot.runtime.session.path),
            "id": "r2",
        },
    )
    assert frame["ok"] is True


def _spy(server) -> list[dict]:
    """Record the requests that reach one slot's server."""
    received: list[dict] = []

    async def fake_handle_line(line: str) -> bool:
        received.append(json.loads(line))
        return True

    server._handle_line = fake_handle_line
    return received


async def test_prompt_tagged_with_a_sibling_session_reaches_the_sibling_slot(
    host_env,
):
    host = host_env.build()
    sibling = await _spawn_sibling(host, host_env)
    await _switch_back(host, host_env)
    original_hits = _spy(host_env.slot.server)
    sibling_hits = _spy(sibling.server)
    request = {
        "type": "prompt",
        "text": "hi",
        "session": str(_other_session(host_env)),
        "id": "p",
    }

    await host._handle_line(json.dumps(request))

    assert len(sibling_hits) == 1
    assert sibling_hits[0]["id"] == "p"
    assert original_hits == []
    assert host_env.slot.chat.busy is True


async def test_transcript_and_tools_tagged_with_a_session_serve_its_slot(host_env):
    host = host_env.build()
    sibling = await _spawn_sibling(host, host_env)
    transcript = await call(
        host,
        host_env.writes,
        {"type": "transcript", "session": str(_other_session(host_env)), "id": "t"},
    )
    assert transcript["ok"] is True
    assert transcript["session"] == str(sibling.runtime.session.path)

    tools = await call(
        host,
        host_env.writes,
        {"type": "tools", "session": str(_other_session(host_env)), "id": "t2"},
    )
    assert tools["ok"] is True
    assert tools["session"] == str(sibling.runtime.session.path)


async def test_tagged_request_without_a_resident_slot_is_refused(host_env):
    host = host_env.build()
    active_hits = _spy(host_env.slot.server)

    frame = await call(
        host,
        host_env.writes,
        {"type": "status", "session": "/tmp/none.jsonl", "id": "s"},
    )

    assert frame["type"] == "error"
    assert frame["code"] == "session_invalid"
    assert active_hits == []


async def test_session_tag_from_another_project_is_refused(host_env):
    host = host_env.build()
    await _spawn_sibling(host, host_env)
    sibling_path = str(_other_session(host_env))
    hits = _spy(host_env.slot.server)

    frame = await call(
        host,
        host_env.writes,
        {"type": "status", "session": sibling_path, "project": str(host_env.beta), "id": "s"},
    )

    assert frame["type"] == "error"
    assert frame["code"] == "session_invalid"
    assert hits == []


async def test_untagged_requests_keep_active_slot_behavior(host_env):
    host = host_env.build()
    sibling = await _spawn_sibling(host, host_env)
    await _switch_back(host, host_env)
    active_hits = _spy(host_env.slot.server)
    sibling_hits = _spy(sibling.server)

    await host._handle_line(
        json.dumps({"type": "prompt", "text": "hi", "id": "u"})
    )

    assert len(active_hits) == 1
    assert active_hits[0]["id"] == "u"
    assert sibling_hits == []


async def test_new_session_on_a_busy_tagged_holder_spawns_a_sibling(host_env):
    host = host_env.build()
    busy = host_env.slot
    busy.chat.busy = True
    busy_hits = _spy(busy.server)

    frame = await call(
        host,
        host_env.writes,
        {
            "type": "session",
            "op": "new",
            "session": str(busy.runtime.session.path),
            "id": "n1",
        },
    )

    assert frame["ok"] is True
    new_path = frame["path"]
    assert new_path != str(busy.runtime.session.path)
    # The busy run is untouched; the sibling is the project's active slot.
    assert busy.chat.busy is True
    assert busy_hits == []
    sibling = host._active_by_project[project_key(host_env.alpha)]
    assert sibling is not busy
    assert str(sibling.runtime.session.path) == new_path


async def test_untagged_new_session_on_busy_keeps_the_kernel_refusal(host_env):
    host = host_env.build()
    busy = host_env.slot
    busy.chat.busy = True

    frame = await call(
        host,
        host_env.writes,
        {"type": "session", "op": "new", "id": "n2"},
    )

    # v2 parity: the kernel refuses a busy switch; the host never silently
    # moves an untagged client's active slot.
    assert frame["type"] == "error"
    assert frame["code"] == "session_lease_busy"
    assert len(host._slots) == 1, "untagged requests must not spawn siblings"


async def test_import_on_a_busy_tagged_holder_loads_in_a_sibling(host_env):
    host = host_env.build()
    busy = host_env.slot
    busy.chat.busy = True
    incoming = host_env.tmp_path / "incoming.jsonl"
    incoming.write_text("{}", encoding="utf-8")

    frame = await call(
        host,
        host_env.writes,
        {
            "type": "session",
            "op": "import",
            "path": str(incoming),
            "session": str(busy.runtime.session.path),
            "id": "i1",
        },
    )

    assert frame["ok"] is True
    # The import landed in the idle sibling, not the busy holder: the sibling
    # is the project's active slot and holds the imported session, while the
    # busy run keeps running untouched.
    sibling = host._active_by_project[project_key(host_env.alpha)]
    assert sibling is not busy
    assert sibling.runtime.session.path == incoming.resolve()
    assert busy.chat.busy is True


async def test_idle_tagged_new_session_still_switches_in_place(host_env):
    host = host_env.build()
    slot = host_env.slot

    frame = await call(
        host,
        host_env.writes,
        {
            "type": "session",
            "op": "new",
            "session": str(slot.runtime.session.path),
            "id": "n3",
        },
    )

    # An idle holder keeps the old in-place behavior: forwarded verbatim, the
    # kernel switches this slot to the fresh session, no sibling appears.
    assert frame["ok"] is True
    assert frame["path"].endswith("stub-new1.jsonl")
    assert str(slot.runtime.session.path) == frame["path"]
    assert len(host._slots) == 1
    assert frame["ok"] is True
    assert frame["path"].endswith("stub-new1.jsonl")
    assert len(host._slots) == 1
