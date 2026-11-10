"""The zen_fleet RPC ops: enable/disable/state/transcript and roster frames."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from zenagent.app.bootstrap import PreparedFrontend
from zenagent.cli.rpc.slot import RuntimeSlot


class FakeAgent:
    def __init__(self) -> None:
        self.is_streaming = False

    def subscribe(self, listener) -> None:
        pass

    def abort(self) -> None:
        pass

    async def wait_for_idle(self) -> None:
        pass


class FakeChat:
    def __init__(self) -> None:
        self.busy = False
        self.steered: list[str] = []

    def steer(self, text: str) -> None:
        self.steered.append(text)

    def reload_transcript(self) -> None:
        pass

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


class FakeRuntime:
    def __init__(self, root: Path, sessions_dir: Path) -> None:
        self.root = root
        self.cwd = root
        self.sessions_dir = sessions_dir
        self._seq = 0
        self.session = SimpleNamespace(path=self._new_path())
        self.agent = FakeAgent()
        self.zen_fleet = None
        self.closed = False
        self.recomposed = 0

    def _new_path(self) -> Path:
        self._seq += 1
        return self.sessions_dir / f"20261111T00000{self._seq}-s.jsonl"

    def use_new_session(self) -> SimpleNamespace:
        self.session = SimpleNamespace(path=self._new_path())
        return self.session

    async def switch_session(self, path, *, carry_todos: bool = False) -> None:
        self.session = SimpleNamespace(path=Path(path))

    def _recompose(self) -> None:
        self.recomposed += 1

    def _note(self, text: str) -> None:
        pass

    async def close(self) -> None:
        self.closed = True


class FakeZenFleet:
    """The shape ops_zen touches; real fleet behavior lives in test_zen_fleet."""

    def __init__(self) -> None:
        self.closed = False
        self._active = 0

    def has_active(self) -> bool:
        return self._active > 0

    def roster(self) -> list[dict]:
        return [{"path": "x", "title": "x", "focus": "", "role": "worker", "status": "idle", "last_report": ""}]

    async def close(self) -> None:
        self.closed = True


def _collect(writes: list[dict]):
    def write_line(payload: str) -> None:
        writes.append(json.loads(payload))

    return write_line


@pytest.fixture
async def slot_env(tmp_path):
    root = tmp_path / "project"
    (root / ".git").mkdir(parents=True)
    from zenagent.project import project_sessions_dir

    sessions_dir = project_sessions_dir(root)
    sessions_dir.mkdir(parents=True, exist_ok=True)
    runtime = FakeRuntime(root, sessions_dir)
    runtime.session.path.touch()  # a live session's file exists on disk
    chat = FakeChat()
    prepared = PreparedFrontend(
        runtime=runtime, broker=FakeBroker(), chat=chat, services=FakeServices()
    )
    writes: list[dict] = []
    slot = RuntimeSlot.adopt(prepared, write_line=_collect(writes))

    async def call(request: dict) -> dict:
        assert await slot.server._handle_line(json.dumps(request))
        return writes[-1]

    return SimpleNamespace(
        slot=slot, runtime=runtime, chat=chat, writes=writes, call=call,
        sessions_dir=sessions_dir, root=root,
    )


def _worker_session(sessions_dir: Path) -> Path:
    path = sessions_dir / "20261111-002014-worker.jsonl"
    session_lines = [
        json.dumps({"role": "user", "content": [{"type": "text", "text": "干活"}], "timestamp": 0}),
        json.dumps(
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "做完了"}],
                "provider": "fake",
                "model": "fake",
                "usage": {"input": 1, "output": 1, "cache_read": 0, "cache_write": 0, "cost": {"total": 0.0}},
                "stop_reason": "stop",
                "timestamp": 1,
            }
        ),
    ]
    path.write_text("\n".join(session_lines) + "\n", encoding="utf-8")
    return path


async def test_enable_switches_to_a_fresh_router_and_emits_roster(slot_env):
    previous = slot_env.runtime.session.path
    result = await slot_env.call({"type": "zen_fleet", "op": "enable", "id": "z1"})
    assert result["ok"] is True and result["enabled"] is True
    assert slot_env.runtime.session.path != previous
    assert slot_env.runtime.zen_fleet is not None
    assert slot_env.runtime.recomposed > 0
    frames = [f for f in slot_env.writes if f.get("type") == "zen_fleet" and "roster" in f]
    assert frames
    assert all("project" in f and "session" in f for f in frames)
    roster_file = slot_env.sessions_dir / ".zen.json"
    assert json.loads(roster_file.read_text())[slot_env.runtime.session.path.stem]["role"] == "router"


async def test_enable_refuses_a_busy_slot(slot_env):
    slot_env.chat.busy = True
    result = await slot_env.call({"type": "zen_fleet", "op": "enable", "id": "z2"})
    assert result["type"] == "error" and result["code"] == "zen_busy"
    assert slot_env.runtime.zen_fleet is None


async def test_disable_restores_previous_session_and_closes_fleet(slot_env):
    previous = str(slot_env.runtime.session.path)
    await slot_env.call({"type": "zen_fleet", "op": "enable", "id": "z3"})
    fleet = FakeZenFleet()
    slot_env.runtime.zen_fleet = fleet
    result = await slot_env.call({"type": "zen_fleet", "op": "disable", "id": "z4"})
    assert result["ok"] is True and result["enabled"] is False
    assert str(slot_env.runtime.session.path) == previous
    assert slot_env.runtime.zen_fleet is None
    assert fleet.closed is True


async def test_enable_reuses_the_existing_router_session(slot_env):
    await slot_env.call({"type": "zen_fleet", "op": "enable", "id": "z5"})
    router_path = slot_env.runtime.session.path
    await slot_env.call({"type": "zen_fleet", "op": "disable", "id": "z6"})
    await slot_env.call({"type": "zen_fleet", "op": "enable", "id": "z7"})
    assert slot_env.runtime.session.path == router_path


async def test_state_reports_enabled_and_roster(slot_env):
    disabled = await slot_env.call({"type": "zen_fleet", "op": "state", "id": "z8"})
    assert disabled["enabled"] is False and disabled["roster"] == []
    await slot_env.call({"type": "zen_fleet", "op": "enable", "id": "z9"})
    enabled = await slot_env.call({"type": "zen_fleet", "op": "state", "id": "z10"})
    assert enabled["enabled"] is True and isinstance(enabled["roster"], list)


async def test_transcript_reads_an_arbitrary_session_file(slot_env):
    path = _worker_session(slot_env.sessions_dir)
    result = await slot_env.call(
        {"type": "zen_fleet", "op": "transcript", "path": str(path), "id": "z11"}
    )
    assert result["ok"] is True
    assert result["total"] >= 2
    assert any(view.get("text") == "做完了" for view in result["transcript"] if "text" in view)


async def test_slot_pins_while_workers_are_active(slot_env):
    assert slot_env.slot.pinned is False
    await slot_env.call({"type": "zen_fleet", "op": "enable", "id": "z12"})
    fleet = FakeZenFleet()
    slot_env.runtime.zen_fleet = fleet
    fleet._active = 1
    assert slot_env.slot.pinned is True
    fleet._active = 0
    assert slot_env.slot.pinned is False


async def test_slot_close_shuts_the_fleet_down(slot_env):
    await slot_env.call({"type": "zen_fleet", "op": "enable", "id": "z13"})
    fleet = FakeZenFleet()
    slot_env.runtime.zen_fleet = fleet
    await slot_env.slot.close()
    assert fleet.closed is True


async def test_unknown_zen_op_is_a_refusal(slot_env):
    result = await slot_env.call({"type": "zen_fleet", "op": "dance", "id": "z14"})
    assert result["type"] == "error"
