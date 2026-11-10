"""The ``swarm`` RPC family: kind contract, ops and prefix-routed approvals."""

from __future__ import annotations

import json
import re
from pathlib import Path

from zenagent.app.runtime import RuntimeOptions
from zenagent.app.swarm.room import TaskRecord
from zenagent.cli.rpc.host import RpcHost

ROOT = Path(__file__).resolve().parents[1]
TS_PATH = ROOT / "desktop" / "src" / "rpc" / "types.ts"


def _collect(writes: list[dict]):
    def write(line: str) -> None:
        writes.append(json.loads(line))

    return write


def _host(tmp_path: Path) -> tuple[RpcHost, list[dict]]:
    writes: list[dict] = []
    host = RpcHost(
        initial=None,
        options=RuntimeOptions(root=tmp_path),
        read_line=None,
        write_line=_collect(writes),
    )
    return host, writes


async def _call(host: RpcHost, request: dict) -> None:
    assert await host._handle_line(json.dumps(request))


class FakeSwarm:
    def __init__(self) -> None:
        self.started = False
        self.posts: list[str] = []
        self.approvals: list[tuple[str, str]] = []
        self.role_updates: list[tuple[str, dict]] = []
        self.role_deletes: list[str] = []

    async def start(self) -> None:
        self.started = True

    def roles(self) -> list[dict]:
        return [{"id": "research", "name": "Research"}]

    def agents_state(self) -> list[dict]:
        return [
            {
                "agent": "research",
                "name": "Research",
                "state": "idle",
                "task": None,
                "pendingApply": False,
            }
        ]

    def tasks(self) -> list[dict]:
        return [{"id": "t_0", "assignee": "research", "state": "queued"}]

    def messages(self, since=None) -> list[dict]:
        return [{"id": "m_0", "text": "hi", "author": {"kind": "user", "id": "you"}}]

    async def post(self, text: str) -> list[TaskRecord]:
        self.posts.append(text)
        return [
            TaskRecord(
                id="t_1",
                assignee="research",
                state="queued",
                source_message_id="m_1",
                chain_id="c_1",
                chain_depth=1,
            )
        ]

    def task_view(self, task: TaskRecord) -> dict:
        return {
            "id": task.id,
            "assignee": task.assignee,
            "state": task.state,
            "sourceMessageId": task.source_message_id,
            "chainId": task.chain_id,
            "chainDepth": task.chain_depth,
        }

    async def cancel(self, task_id: str) -> bool:
        return True

    async def retry(self, task_id: str) -> TaskRecord | None:
        return None

    async def reset(self, agent_id: str) -> bool:
        return True

    async def update_role(self, role_id: str, manifest: dict):
        self.role_updates.append((role_id, manifest))

    async def delete_role(self, role_id: str) -> None:
        self.role_deletes.append(role_id)

    async def reload(self) -> list[str]:
        return []

    def resolve_approval(self, request_id: str, decision: str) -> bool:
        self.approvals.append((request_id, decision))
        return True

    def resolve_ask(self, request_id: str, answers: list) -> bool:
        return False


def test_swarm_kind_contract():
    package = ROOT / "zenagent" / "cli" / "rpc"
    source = "\n".join(sorted(path.read_text() for path in package.glob("*.py")))
    kinds = set(re.findall(r'kind == "([a-z_]+)"', source))
    assert "swarm" in kinds

    ts = TS_PATH.read_text()
    match = re.search(r"export const REQUEST_KINDS = \[([^\]]*)\]", ts, re.DOTALL)
    assert match, "desktop/src/rpc/types.ts lost REQUEST_KINDS"
    assert "'swarm'" in match.group(1)


async def test_list_returns_roles_tasks_and_messages(tmp_path):
    host, writes = _host(tmp_path)
    fake = FakeSwarm()
    host._swarm = fake  # type: ignore[assignment]

    await _call(host, {"type": "swarm", "op": "list", "id": "s1"})

    result = writes[-1]
    assert fake.started is True
    assert result["type"] == "result"
    assert result["id"] == "s1"
    assert result["ok"] is True
    assert result["roles"][0]["id"] == "research"
    assert result["agents"][0]["state"] == "idle"
    assert result["tasks"][0]["id"] == "t_0"
    assert result["messages"][0]["id"] == "m_0"


async def test_post_creates_tasks(tmp_path):
    host, writes = _host(tmp_path)
    fake = FakeSwarm()
    host._swarm = fake  # type: ignore[assignment]

    await _call(host, {"type": "swarm", "op": "post", "id": "s2", "text": "@research hi"})

    result = writes[-1]
    assert fake.posts == ["@research hi"]
    assert result["ok"] is True
    assert [task["id"] for task in result["tasks"]] == ["t_1"]
    assert result["tasks"][0]["assignee"] == "research"


async def test_roles_create_uses_role_id_not_request_id(tmp_path):
    host, writes = _host(tmp_path)
    fake = FakeSwarm()
    host._swarm = fake  # type: ignore[assignment]

    await _call(
        host,
        {
            "type": "swarm",
            "op": "roles",
            "action": "create",
            "id": "r7",
            "roleId": "reviewer",
            "manifest": {"name": "Reviewer"},
        },
    )

    result = writes[-1]
    assert result["ok"] is True
    assert result["id"] == "r7"
    assert fake.role_updates == [("reviewer", {"name": "Reviewer"})]


async def test_roles_delete_uses_role_id_not_request_id(tmp_path):
    host, writes = _host(tmp_path)
    fake = FakeSwarm()
    host._swarm = fake  # type: ignore[assignment]

    await _call(
        host,
        {"type": "swarm", "op": "roles", "action": "delete", "id": "r8", "roleId": "reviewer"},
    )

    assert writes[-1]["ok"] is True
    assert fake.role_deletes == ["reviewer"]


async def test_swarm_frames_are_global(tmp_path, monkeypatch):
    created: list = []

    class FrameManager:
        def __init__(self, *, write_frame=None, stream_fn_factory=None, max_concurrent=3):
            self.write_frame = write_frame
            created.append(self)

        async def start(self) -> None:
            self.write_frame(
                {
                    "type": "swarm_message",
                    "swarm": True,
                    "id": "m_1",
                    "text": "hello",
                    "author": {"kind": "system", "id": "system"},
                }
            )

        def roles(self) -> list[dict]:
            return []

        def agents_state(self) -> list[dict]:
            return []

        def tasks(self) -> list[dict]:
            return []

        def messages(self, since=None) -> list[dict]:
            return []

    monkeypatch.setattr(
        "zenagent.cli.rpc.ops_swarm.SwarmManager", FrameManager
    )
    host, writes = _host(tmp_path)

    await _call(host, {"type": "swarm", "op": "list", "id": "s3"})

    frames = [frame for frame in writes if frame.get("type") == "swarm_message"]
    assert len(frames) == 1
    assert frames[0]["swarm"] is True
    assert "project" not in frames[0]
    assert "session" not in frames[0]
    assert writes[-1]["type"] == "result"


async def test_swarm_approval_routes_by_prefix(tmp_path):
    host, writes = _host(tmp_path)
    fake = FakeSwarm()
    host._swarm = fake  # type: ignore[assignment]

    await _call(
        host,
        {
            "type": "approval",
            "id": "a1",
            "request_id": "swarm:a1",
            "decision": "allow",
        },
    )

    assert fake.approvals == [("swarm:a1", "allow")]
    assert writes[-1] == {"type": "result", "id": "a1", "ok": True}
