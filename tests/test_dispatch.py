"""One dispatch entry, mode-specific strategies."""

from __future__ import annotations

import pytest

from zenagent.app.agent_profile import Supervisor
from zenagent.app.dispatch import (
    DispatchUnavailable,
    SwarmDispatcher,
    ZenDispatcher,
)


class _Fleet:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def dispatch(self, session: str, task: str) -> str:
        self.calls.append((session, task))
        return "working"


class _Manager:
    def __init__(self) -> None:
        self.texts: list[str] = []
        self.options: list[dict] = []
        self.started = True

    def roles(self) -> list[dict]:
        return [{"id": "reviewer"}]

    async def post(self, text, *, ref=None, images=None) -> list[dict]:
        self.texts.append(text)
        self.options.append({"ref": ref, "images": images})
        return [{"id": "t1"}]

    def task_view(self, task: dict) -> dict:
        return {"id": task["id"], "state": "queued"}


async def test_zen_dispatcher_names_the_worker():
    fleet = _Fleet()
    result = await ZenDispatcher(fleet).dispatch(
        prompt="inspect", session="/tmp/sessions/w1.jsonl"
    )
    assert fleet.calls == [("/tmp/sessions/w1.jsonl", "inspect")]
    assert result == {
        "dispatched": True,
        "session": "/tmp/sessions/w1.jsonl",
        "agent": "w1",
        "status": "working",
    }


async def test_swarm_dispatcher_prefixes_a_known_role():
    manager = _Manager()
    result = await SwarmDispatcher(manager).dispatch(
        prompt="look at the diff", role="reviewer"
    )
    assert manager.texts == ["@reviewer look at the diff"]
    assert result == {
        "dispatched": True,
        "tasks": [{"id": "t1", "state": "queued"}],
        "taskIds": ["t1"],
    }


async def test_swarm_dispatcher_refuses_an_unknown_role():
    with pytest.raises(ValueError, match="unknown swarm role"):
        await SwarmDispatcher(_Manager()).dispatch(prompt="x", role="nobody")


async def test_swarm_dispatcher_posts_without_a_role():
    manager = _Manager()
    await SwarmDispatcher(manager).dispatch(prompt="plain")
    assert manager.texts == ["plain"]
    assert manager.options == [{"ref": None, "images": None}]


async def test_swarm_dispatcher_forwards_quote_and_images():
    manager = _Manager()
    await SwarmDispatcher(manager).dispatch(
        prompt="plain", ref="m1", images=["/tmp/a.png"]
    )
    assert manager.options == [{"ref": "m1", "images": ["/tmp/a.png"]}]


async def test_supervisor_mounts_and_unmounts_dispatch(tmp_path):
    class _Stub:
        mode = "zen"

        async def dispatch(self, **payload):
            return {"seen": payload}

    supervisor = Supervisor(tmp_path)
    with pytest.raises(DispatchUnavailable):
        await supervisor.dispatch("zen", prompt="x")
    supervisor.attach_dispatcher(_Stub())
    assert await supervisor.dispatch("zen", prompt="x") == {"seen": {"prompt": "x"}}
    assert supervisor.detach_dispatcher("zen") is not None
    with pytest.raises(DispatchUnavailable):
        await supervisor.dispatch("zen", prompt="x")


async def test_swarm_dispatcher_refuses_a_closed_room():
    manager = _Manager()
    manager.started = False
    with pytest.raises(DispatchUnavailable, match="closed"):
        await SwarmDispatcher(manager).dispatch(prompt="x")
