"""One resident project: wire tags, scoped ids, and lifecycle wiring."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from zenagent.app.bootstrap import PreparedFrontend
from zenagent.cli.rpc.slot import RuntimeSlot
from zenagent.project import project_key


class FakeAgent:
    def __init__(self) -> None:
        self.messages: list = []
        self.is_streaming = False
        self.tools: list = []
        self.termination = "stop"

    def subscribe(self, listener) -> None:
        self._listener = listener

    def abort(self) -> None:
        pass


class FakeChat:
    busy = False

    def reload_transcript(self) -> None:
        pass

    async def wait_for_idle(self) -> None:
        pass


class FakeRuntime:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.cwd = self.root
        self.agent = FakeAgent()
        self.ask_handler = None
        self.closed = False

    def _note(self, text: str) -> None:
        pass

    async def close(self) -> None:
        self.closed = True


class FakeBroker:
    def __init__(self) -> None:
        self.confirm = None

    def set_confirm(self, fn) -> None:
        self.confirm = fn


class FakeServices:
    models = None
    sessions = None


def _prepared(root: Path) -> tuple[PreparedFrontend, FakeRuntime, FakeBroker]:
    runtime = FakeRuntime(root)
    broker = FakeBroker()
    return (
        PreparedFrontend(
            runtime=runtime,
            broker=broker,
            chat=FakeChat(),
            services=FakeServices(),
        ),
        runtime,
        broker,
    )


def _collect(writes: list[dict]):
    def write(line: str) -> None:
        writes.append(json.loads(line))

    return write


def _adopt(root: Path, writes: list[dict]) -> RuntimeSlot:
    prepared, runtime, broker = _prepared(root)
    slot = RuntimeSlot.adopt(prepared, write_line=_collect(writes))
    assert broker.confirm == slot.server.confirm
    assert runtime.ask_handler == slot.server.ask
    return slot


async def test_slot_tags_frames_and_scopes_approval_ids(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    writes: list[dict] = []
    slot = _adopt(root, writes)

    pending = asyncio.create_task(slot.server.confirm("bash", {"command": "ls"}))
    await asyncio.sleep(0)
    frame = writes[0]
    assert frame["project"] == str(root.resolve())
    assert frame["event"]["type"] == "approval_request"
    assert frame["event"]["request_id"] == f"{slot.key}:a0"

    slot.server._resolve_approval(
        {"request_id": f"{slot.key}:a0", "decision": "deny", "id": "r1"}
    )
    assert await pending == "deny"
    assert writes[-1]["project"] == str(root.resolve())
    assert writes[-1]["ok"] is True


async def test_slot_scopes_ask_ids_too(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    writes: list[dict] = []
    slot = _adopt(root, writes)
    questions = [
        {"id": "q1", "question": "Continue?", "options": [{"label": "Yes"}]}
    ]

    pending = asyncio.create_task(slot.server.ask(questions))
    await asyncio.sleep(0)
    request_id = writes[0]["event"]["request_id"]
    assert request_id == f"{slot.key}:q0"

    slot.server._resolve_ask(
        {"request_id": request_id, "answers": [["Yes"]], "id": "r2"}
    )
    assert await pending == [{"id": "q1", "selected": ["Yes"]}]


async def test_slot_id_uses_the_project_key(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    slot = _adopt(root, [])
    assert slot.key == project_key(root.resolve())
    assert slot.server.project == str(root.resolve())


async def test_slot_close_closes_the_runtime(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    prepared, runtime, _broker = _prepared(root)
    slot = RuntimeSlot.adopt(prepared, write_line=[].append)

    await slot.close()

    assert runtime.ask_handler is None
    assert runtime.closed is True
