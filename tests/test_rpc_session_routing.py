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

    def reload_transcript(self) -> None:
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
    async def fake_prepare(options, **kwargs):
        root = Path(options.root).resolve()
        runtime = FakeRuntime(root)
        resume = getattr(options, "resume", None)
        if resume is not None:
            runtime.session = SimpleNamespace(
                path=Path(resume).resolve(),
                ensure_loaded=lambda: None,
                records=[],
            )
        return PreparedFrontend(
            runtime=runtime,
            broker=FakeBroker(),
            chat=FakeChat(),
            services=FakeServices(),
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
