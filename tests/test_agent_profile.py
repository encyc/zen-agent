"""AgentProfile and the project Supervisor registry."""

from __future__ import annotations

from pathlib import Path

from zenagent.app.agent_profile import AgentProfile, Supervisor


class _FakeAgent:
    is_streaming = False


class _FakeRuntime:
    def __init__(self) -> None:
        self.agent = _FakeAgent()
        self.current_run_id = "r_1"
        self.closed = False
        self.run_scope = {}

    async def close(self) -> None:
        self.closed = True


class _FakeSession:
    def __init__(self, path: Path) -> None:
        self.path = path


def test_profile_builds_a_run_scope():
    profile = AgentProfile(id="research", kind="swarm", label="Research")
    assert profile.run_scope() == {
        "kind": "swarm",
        "agentId": "research",
        "label": "Research",
    }
    assert profile.run_scope(workItemId="w1") == {
        "kind": "swarm",
        "agentId": "research",
        "label": "Research",
        "workItemId": "w1",
    }


async def test_supervisor_registers_snapshots_and_closes(tmp_path):
    supervisor = Supervisor(tmp_path)
    runtime = _FakeRuntime()
    session = _FakeSession(tmp_path / "s.jsonl")
    supervisor.register(
        "research@w1",
        profile=AgentProfile(id="research", kind="swarm", label="Research"),
        runtime=runtime,
        session=session,
        workItemId="w1",
    )

    [handle] = supervisor.handles()
    assert handle.key == "research@w1"
    assert runtime.run_scope["agentId"] == "research"
    assert supervisor.snapshots() == [
        {
            "key": "research@w1",
            "agent": "research",
            "kind": "swarm",
            "label": "Research",
            "state": "idle",
            "session": str(session.path),
            "runId": "r_1",
            "workItemId": "w1",
        }
    ]

    assert supervisor.unregister("missing") is None
    assert supervisor.unregister("research@w1") is handle
    assert supervisor.handles() == []
    supervisor.register(
        "again",
        profile=AgentProfile(id="again"),
        runtime=runtime,
        session=session,
    )
    await supervisor.close()
    assert runtime.closed is True
    assert supervisor.handles() == []
