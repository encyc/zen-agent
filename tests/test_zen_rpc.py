"""Host-level zen: a project's router lives in its own slot.

Entering zen must not abort the coding session's run (the zen_busy refusal is
gone), so every enable either reuses the slot already holding the router
session or opens a sibling slot for it.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from zenagent.app.bootstrap import PreparedFrontend
from zenagent.app.runtime import RuntimeOptions
from zenagent.app.workspace_state import load_workspace
from zenagent.cli.rpc.host import RpcHost
from zenagent.cli.rpc.slot import RuntimeSlot
from zenagent.project import project_key, project_sessions_dir


class FakeAgent:
    def __init__(self) -> None:
        self.messages: list = []
        self.is_streaming = False
        self.termination = "stop"
        self.tools = [SimpleNamespace(name="bash", description="", approval="read")]

    def subscribe(self, listener) -> None:
        self._listener = listener

    def abort(self) -> None:
        pass

    async def wait_for_idle(self) -> None:
        pass


class FakeChat:
    def __init__(self) -> None:
        self.busy = False
        self.steered: list[str] = []
        self.reloaded: list[dict] = []

    def steer(self, text: str) -> None:
        self.steered.append(text)

    def reload_transcript(self, *, drop_pending: bool = False) -> None:
        self.reloaded.append({"drop_pending": drop_pending})

    async def wait_for_idle(self) -> None:
        pass


class FakeBroker:
    def __init__(self) -> None:
        self.confirm = None

    def set_confirm(self, fn) -> None:
        self.confirm = fn


class FakeServices:
    models = None
    sessions = None


class FakeUserConfig:
    zen_max_concurrent = 3
    zen_batch_window_s = 0.5
    task_timeout_s = 0
    task_max_cost: float | None = 2.0
    task_max_turns = 9
    zen_report_limit = 800
    zen_worker_mode = "inherit"


class FakeRuntime:
    """The shape the host touches: session, agent, chat wiring, recompose."""

    def __init__(self, root: Path, name: str) -> None:
        self.root = Path(root).resolve()
        self.cwd = self.root
        self.session = SimpleNamespace(
            path=project_sessions_dir(self.root) / f"{name}.jsonl"
        )
        self.agent = FakeAgent()
        self.ask_handler = None
        self.mode = "ask"
        self.user_config = FakeUserConfig()
        self.zen_fleet = None
        self.warnings: list[str] = []
        self.closed = False
        self.recomposed = 0

    def _recompose(self) -> None:
        self.recomposed += 1

    def _note(self, text: str) -> None:
        pass

    async def close(self) -> None:
        self.closed = True


class FakeFleet:
    """The shape ops_zen touches; real fleet behavior lives in test_zen_fleet."""

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.closed = False
        self._active = 0
        self.calls: list[tuple[str, str]] = []

    def has_active(self) -> bool:
        return self._active > 0

    async def dispatch(self, session: str, task: str) -> str:
        self.calls.append((session, task))
        return "working"

    def roster(self) -> list[dict]:
        return [
            {
                "path": self.kwargs["router_path"],
                "title": "router",
                "focus": "",
                "role": "router",
                "status": "idle",
                "last_report": "",
            }
        ]

    async def close(self) -> None:
        self.closed = True


def _collect(writes: list[dict]):
    def write(line: str) -> None:
        writes.append(json.loads(line))

    return write


@pytest.fixture
async def zen_env(tmp_path, monkeypatch):
    created: list[FakeRuntime] = []
    fleets: list[FakeFleet] = []
    seq = {"n": 0}

    async def fake_prepare(options, **kwargs):
        seq["n"] += 1
        root = Path(options.root).resolve()
        runtime = FakeRuntime(root, f"{root.name}-{seq['n']}")
        resume = getattr(options, "resume", None)
        if resume is not None:
            runtime.session = SimpleNamespace(path=Path(resume).resolve())
        created.append(runtime)
        return PreparedFrontend(
            runtime=runtime,
            broker=FakeBroker(),
            chat=FakeChat(),
            services=FakeServices(),
        )

    monkeypatch.setattr("zenagent.cli.rpc.host.prepare", fake_prepare)
    monkeypatch.setattr("zenagent.cli.rpc.ops_zen.ZenFleet", FakeFleet)

    root = tmp_path / "project"
    (root / ".git").mkdir(parents=True)
    sessions_dir = project_sessions_dir(root)
    sessions_dir.mkdir(parents=True, exist_ok=True)
    prepared = await fake_prepare(RuntimeOptions(root=root))
    prepared.runtime.session.path.touch()  # a live session's file exists on disk
    writes: list[dict] = []
    slot = RuntimeSlot.adopt(prepared, write_line=_collect(writes))
    host = RpcHost(
        initial=slot,
        options=RuntimeOptions(root=root),
        read_line=None,
        write_line=_collect(writes),
    )

    async def call(request: dict) -> dict:
        assert await host._handle_line(json.dumps(request))
        return writes[-1]

    return SimpleNamespace(
        host=host,
        coding=slot,
        writes=writes,
        call=call,
        root=root,
        sessions_dir=sessions_dir,
        created=created,
        fleets=fleets,
    )


def _worker_session(sessions_dir: Path) -> Path:
    path = sessions_dir / "20261111-002014-worker.jsonl"
    session_lines = [
        json.dumps(
            {
                "role": "user",
                "content": [{"type": "text", "text": "干活"}],
                "timestamp": 0,
            }
        ),
        json.dumps(
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "做完了"}],
                "provider": "fake",
                "model": "fake",
                "usage": {
                    "input": 1,
                    "output": 1,
                    "cache_read": 0,
                    "cache_write": 0,
                    "cost": {"total": 0.0},
                },
                "stop_reason": "stop",
                "timestamp": 1,
            }
        ),
    ]
    path.write_text("\n".join(session_lines) + "\n", encoding="utf-8")
    return path


def _router(env) -> RuntimeSlot | None:
    return env.host._zen_slot_for(project_key(env.root))


async def test_enable_opens_a_router_slot_and_never_touches_the_coding_one(zen_env):
    coding = zen_env.coding
    coding_path = str(coding.runtime.session.path)
    # A run owns the coding slot: the old slot-level ops refused here.
    coding.chat.busy = True

    frame = await zen_env.call({"type": "zen_fleet", "op": "enable", "id": "z1"})

    assert frame["ok"] is True and frame["enabled"] is True
    router = _router(zen_env)
    assert router is not None and router is not coding
    assert str(coding.runtime.session.path) == coding_path
    assert router.runtime.zen_fleet is not None
    assert router.runtime.recomposed > 0
    assert frame["session"] == str(router.runtime.session.path)
    assert frame["router"] == frame["session"]
    assert frame["roster"]
    # The foreground follows the router; the coding slot stays resident.
    assert zen_env.host.active is router
    assert coding.key in zen_env.host._slots
    # The roster sidecar records the router role.
    roster_file = zen_env.sessions_dir / ".zen.json"
    recorded = json.loads(roster_file.read_text())
    assert recorded[router.runtime.session.path.stem]["role"] == "router"


async def test_enable_pushes_a_roster_frame_tagged_with_the_project(zen_env):
    await zen_env.call({"type": "zen_fleet", "op": "enable", "id": "z1"})
    frames = [f for f in zen_env.writes if f.get("type") == "zen_fleet"]
    assert frames
    assert frames[-1]["enabled"] is True
    assert frames[-1]["project"] == str(zen_env.root.resolve())
    assert frames[-1]["session"] == str(_router(zen_env).runtime.session.path)


async def test_enable_is_idempotent_and_never_orphans_the_fleet(zen_env):
    await zen_env.call({"type": "zen_fleet", "op": "enable", "id": "z1"})
    router = _router(zen_env)
    fleet = router.runtime.zen_fleet
    slots = len(zen_env.host._slots)

    frame = await zen_env.call({"type": "zen_fleet", "op": "enable", "id": "z2"})

    assert frame["ok"] is True and frame["enabled"] is True
    assert len(zen_env.host._slots) == slots
    assert _router(zen_env) is router
    assert router.runtime.zen_fleet is fleet
    assert fleet.closed is False


async def test_disable_restores_the_coding_slot_and_closes_the_fleet(zen_env):
    coding_path = str(zen_env.coding.runtime.session.path)
    await zen_env.call({"type": "zen_fleet", "op": "enable", "id": "z1"})
    router = _router(zen_env)
    fleet = router.runtime.zen_fleet

    frame = await zen_env.call({"type": "zen_fleet", "op": "disable", "id": "z2"})

    assert frame["ok"] is True and frame["enabled"] is False
    assert frame["session"] == coding_path
    assert fleet.closed is True
    assert router.runtime.zen_fleet is None
    assert _router(zen_env) is None
    assert zen_env.host.active is zen_env.coding
    # Nothing was running on the router, so its runtime is freed.
    assert router.runtime.closed is True
    assert router.key not in zen_env.host._slots
    frames = [f for f in zen_env.writes if f.get("type") == "zen_fleet"]
    assert frames[-1]["enabled"] is False and frames[-1]["roster"] == []


async def test_disable_keeps_a_busy_router_resident(zen_env):
    await zen_env.call({"type": "zen_fleet", "op": "enable", "id": "z1"})
    router = _router(zen_env)
    router.chat.busy = True  # the router is still answering

    frame = await zen_env.call({"type": "zen_fleet", "op": "disable", "id": "z2"})

    assert frame["enabled"] is False
    assert zen_env.host.active is zen_env.coding
    assert router.key in zen_env.host._slots
    assert router.runtime.closed is False
    assert router.runtime.zen_fleet is None


async def test_leaving_and_reentering_zen_reuses_the_router_session(zen_env):
    await zen_env.call({"type": "zen_fleet", "op": "enable", "id": "z1"})
    router_path = Path(_router(zen_env).runtime.session.path)
    router_path.touch()  # the router conversation exists on disk
    await zen_env.call({"type": "zen_fleet", "op": "disable", "id": "z2"})

    await zen_env.call({"type": "zen_fleet", "op": "enable", "id": "z3"})

    assert Path(_router(zen_env).runtime.session.path) == router_path
    assert len(zen_env.host._slots) == 2


async def test_state_reports_enabled_and_roster(zen_env):
    before = await zen_env.call({"type": "zen_fleet", "op": "state", "id": "s1"})
    assert before["enabled"] is False
    assert before["session"] == str(zen_env.coding.runtime.session.path)
    assert before["roster"] == []

    await zen_env.call({"type": "zen_fleet", "op": "enable", "id": "z1"})
    after = await zen_env.call({"type": "zen_fleet", "op": "state", "id": "s2"})
    assert after["enabled"] is True
    assert after["router"] == str(_router(zen_env).runtime.session.path)
    assert after["roster"]


async def test_disable_without_zen_is_not_an_error(zen_env):
    frame = await zen_env.call({"type": "zen_fleet", "op": "disable", "id": "z1"})
    assert frame["ok"] is True and frame["enabled"] is False
    assert frame["session"] == str(zen_env.coding.runtime.session.path)


async def test_transcript_reads_an_arbitrary_session_file(zen_env):
    path = _worker_session(zen_env.sessions_dir)
    frame = await zen_env.call(
        {"type": "zen_fleet", "op": "transcript", "id": "t1", "path": str(path)}
    )
    assert frame["ok"] is True
    assert frame["total"] >= 1
    assert frame["hasMoreBefore"] is False


async def test_transcript_needs_a_path(zen_env):
    frame = await zen_env.call({"type": "zen_fleet", "op": "transcript", "id": "t1"})
    assert frame["type"] == "error" and frame["code"] == "zen_invalid"


async def test_unknown_zen_op_is_a_refusal(zen_env):
    frame = await zen_env.call({"type": "zen_fleet", "op": "nope", "id": "z9"})
    assert frame["type"] == "error" and frame["code"] == "zen_invalid"


async def test_fleet_is_built_from_the_router_slot(zen_env):
    await zen_env.call({"type": "zen_fleet", "op": "enable", "id": "z1"})
    router = _router(zen_env)
    fleet = router.runtime.zen_fleet
    assert fleet.kwargs["chat"] is router.chat
    assert fleet.kwargs["confirm"] == router.server.confirm
    assert fleet.kwargs["router_path"] == str(router.runtime.session.path)
    assert fleet.kwargs["sessions_dir"] == zen_env.sessions_dir
    assert fleet.kwargs["max_concurrent"] == FakeUserConfig.zen_max_concurrent
    assert fleet.kwargs["mode"] == "ask"
    # The knobs that only exist at this seam: drop either line in ops_zen and
    # the fleet keeps working, so nothing else would notice.
    assert fleet.kwargs["ask"] == router.server.ask
    assert fleet.kwargs["max_cost"] == FakeUserConfig.task_max_cost
    assert fleet.kwargs["max_turns"] == FakeUserConfig.task_max_turns


async def test_router_slot_pins_while_workers_are_active(zen_env):
    await zen_env.call({"type": "zen_fleet", "op": "enable", "id": "z1"})
    router = _router(zen_env)
    fleet = router.runtime.zen_fleet
    fleet._active = 0
    assert router.pinned is False
    fleet._active = 1
    assert router.pinned is True


async def test_zen_ops_route_by_the_project_field(zen_env, tmp_path):
    """A named project enables zen there, not in the foreground project."""
    other = tmp_path / "other"
    (other / ".git").mkdir(parents=True)
    await zen_env.call({"type": "project", "op": "open", "root": str(other), "id": "o"})
    frame = await zen_env.call(
        {"type": "zen_fleet", "op": "enable", "id": "z1", "project": str(zen_env.root)}
    )
    assert frame["ok"] is True
    assert frame["project"] == str(zen_env.root.resolve())
    assert _router(zen_env) is not None
    other_key = project_key(other)
    assert zen_env.host._zen_slot_for(other_key) is None


async def test_resuming_beside_zen_opens_a_sibling_and_leaves_the_router_alone(zen_env):
    """Browsing to another session must not repurpose the fleet's slot."""
    await zen_env.call({"type": "zen_fleet", "op": "enable", "id": "z1"})
    router = _router(zen_env)
    router_path = str(router.runtime.session.path)
    other = _worker_session(zen_env.sessions_dir)

    frame = await zen_env.call(
        {"type": "session", "op": "resume", "id": "r1", "path": str(other)}
    )

    assert frame["ok"] is True
    assert str(router.runtime.session.path) == router_path
    assert router.runtime.zen_fleet is not None
    assert zen_env.host.active is not router
    assert str(zen_env.host.active.runtime.session.path) == str(other)


async def test_leaving_zen_keeps_the_session_the_person_switched_to(zen_env):
    await zen_env.call({"type": "zen_fleet", "op": "enable", "id": "z1"})
    other = _worker_session(zen_env.sessions_dir)
    await zen_env.call(
        {"type": "session", "op": "resume", "id": "r1", "path": str(other)}
    )

    frame = await zen_env.call({"type": "zen_fleet", "op": "disable", "id": "z2"})

    assert frame["enabled"] is False
    assert frame["session"] == str(other)
    assert _router(zen_env) is None


async def test_enable_and_disable_persist_the_workspace_mode(zen_env):
    await zen_env.call({"type": "zen_fleet", "op": "enable", "id": "w1"})
    assert load_workspace(zen_env.root).mode == "zen"

    await zen_env.call({"type": "zen_fleet", "op": "disable", "id": "w2"})
    assert load_workspace(zen_env.root).mode == "coding"


async def test_supervisor_dispatch_reaches_the_live_fleet(zen_env):
    await zen_env.call({"type": "zen_fleet", "op": "enable", "id": "z1"})
    worker = _worker_session(zen_env.sessions_dir)
    fleet = _router(zen_env).runtime.zen_fleet

    frame = await zen_env.call(
        {
            "type": "supervisor",
            "op": "dispatch",
            "id": "d1",
            "prompt": "inspect",
            "session": str(worker),
        }
    )

    assert frame["ok"] is True
    assert frame["mode"] == "zen"
    assert frame["status"] == "working"
    assert fleet.calls == [(str(worker), "inspect")]
