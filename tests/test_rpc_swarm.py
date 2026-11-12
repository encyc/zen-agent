"""The ``swarm`` RPC family: kind contract, ops and prefix-routed approvals."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from zenagent.app.runtime import RuntimeOptions
from zenagent.app.swarm.roles import agents_root
from zenagent.app.swarm.room import TaskRecord
from zenagent.cli.rpc.host import RpcHost
from zenagent.events import AgentEvent
from zenagent.project import find_project_root, project_key

ROOT = Path(__file__).resolve().parents[1]
TS_PATH = ROOT / "desktop" / "src" / "rpc" / "types.ts"


def _collect(writes: list[dict]):
    def write(line: str) -> None:
        writes.append(json.loads(line))

    return write


def _host(tmp_path: Path, **kwargs) -> tuple[RpcHost, list[dict]]:
    writes: list[dict] = []
    host = RpcHost(
        initial=None,
        options=RuntimeOptions(root=tmp_path),
        read_line=None,
        write_line=_collect(writes),
        **kwargs,
    )
    return host, writes


def _seed_role(root: Path, role_id: str) -> None:
    directory = root / ".zenagent" / "agents" / role_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "manifest.json").write_text(
        json.dumps({"id": role_id, "name": role_id}), encoding="utf-8"
    )
    (directory / "prompt.md").write_text("You are a role.", encoding="utf-8")


async def _call(host: RpcHost, request: dict) -> None:
    assert await host._handle_line(json.dumps(request))


class FakeSwarm:
    def __init__(self) -> None:
        self.started = False
        self.posts: list[tuple[str, str | None, tuple[str, ...]]] = []
        self.approvals: list[tuple[str, str]] = []
        self.role_updates: list[tuple[str, dict]] = []
        self.role_deletes: list[str] = []
        self.warnings: list[str] = []

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

    async def post(self, text: str, *, ref=None, images=None) -> list[TaskRecord]:
        self.posts.append((text, ref, tuple(images or ())))
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

    def task_transcript(self, task_id: str) -> dict:
        return {
            "available": task_id == "t_1",
            "transcript": [{"kind": "assistant", "id": "v1", "text": "hi"}],
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

    def has_active(self) -> bool:
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
    host._swarm[project_key(tmp_path)] = fake

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
    host._swarm[project_key(tmp_path)] = fake

    await _call(host, {"type": "swarm", "op": "post", "id": "s2", "text": "@research hi"})

    result = writes[-1]
    assert fake.posts == [("@research hi", None, ())]
    assert result["ok"] is True
    assert [task["id"] for task in result["tasks"]] == ["t_1"]
    assert result["tasks"][0]["assignee"] == "research"


async def test_post_passes_ref_and_images(tmp_path):
    host, writes = _host(tmp_path)
    fake = FakeSwarm()
    host._swarm[project_key(tmp_path)] = fake

    await _call(
        host,
        {
            "type": "swarm",
            "op": "post",
            "id": "s1",
            "text": "@research hi",
            "ref": "m_0",
            "images": ["/tmp/a.png"],
        },
    )

    assert fake.posts == [("@research hi", "m_0", ("/tmp/a.png",))]
    assert writes[-1]["ok"] is True


async def test_post_rejects_a_bad_ref_type(tmp_path):
    host, writes = _host(tmp_path)
    fake = FakeSwarm()
    host._swarm[project_key(tmp_path)] = fake

    await _call(host, {"type": "swarm", "op": "post", "id": "s1", "text": "hi", "ref": 3})

    assert writes[-1]["type"] == "error"
    assert fake.posts == []


async def test_transcript_returns_the_run_steps(tmp_path):
    host, writes = _host(tmp_path)
    fake = FakeSwarm()
    host._swarm[project_key(tmp_path)] = fake

    await _call(host, {"type": "swarm", "op": "transcript", "id": "s3", "taskId": "t_1"})

    result = writes[-1]
    assert result["ok"] is True
    assert result["id"] == "s3"
    assert result["taskId"] == "t_1"
    assert result["available"] is True
    assert result["transcript"][0]["text"] == "hi"


async def test_roles_create_uses_role_id_not_request_id(tmp_path):
    host, writes = _host(tmp_path)
    fake = FakeSwarm()
    host._swarm[project_key(tmp_path)] = fake

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
    host._swarm[project_key(tmp_path)] = fake

    await _call(
        host,
        {"type": "swarm", "op": "roles", "action": "delete", "id": "r8", "roleId": "reviewer"},
    )

    assert writes[-1]["ok"] is True
    assert fake.role_deletes == ["reviewer"]


async def test_swarm_frames_carry_the_project_tag(tmp_path, monkeypatch):
    created: list = []

    class FrameManager:
        def __init__(
            self,
            *,
            root=None,
            write_frame=None,
            stream_fn_factory=None,
            event_sink=None,
            max_concurrent=3,
            max_chain_depth=8,
            max_chain_cost=5.0,
        ):
            self.write_frame = write_frame
            self.event_sink = event_sink
            self.warnings: list[str] = []
            self.started = False
            created.append(self)

        async def start(self) -> None:
            self.started = True
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
    assert frames[0]["project"] == str(find_project_root(tmp_path))
    assert "session" not in frames[0]
    assert writes[-1]["type"] == "result"


async def test_swarm_approval_routes_by_prefix(tmp_path):
    host, writes = _host(tmp_path)
    fake = FakeSwarm()
    host._swarm[project_key(tmp_path)] = fake

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


async def test_prefixed_approval_reaches_its_background_project(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    _seed_role(a, "alpha")
    _seed_role(b, "beta")
    host, writes = _host(tmp_path)
    try:
        manager = await host._swarm_manager(a)
        await host._swarm_manager(b)  # the active room moved elsewhere
        rid = f"{project_key(a)}:swarm:a1"
        future = asyncio.get_running_loop().create_future()
        manager._approvals[rid] = future

        assert host._resolve_swarm_prompt(
            {"request_id": rid, "id": "x", "decision": "allow"}, "approval"
        )
        assert future.result() == "allow"
        assert writes[-1] == {"type": "result", "id": "x", "ok": True}
    finally:
        await host.close()


async def test_list_and_roles_surface_manager_warnings(tmp_path):
    host, writes = _host(tmp_path)
    fake = FakeSwarm()
    fake.warnings = ["role broken: bad manifest"]
    host._swarm[project_key(tmp_path)] = fake

    await _call(host, {"type": "swarm", "op": "list", "id": "s1"})
    assert writes[-1]["warnings"] == ["role broken: bad manifest"]

    await _call(host, {"type": "swarm", "op": "roles", "id": "s2", "action": "list"})
    assert writes[-1]["warnings"] == ["role broken: bad manifest"]


async def test_role_events_broadcast_as_project_tagged_swarm_event_frames(tmp_path):
    host, writes = _host(tmp_path)

    host._send_role_event(
        "research",
        "t_1",
        AgentEvent(type="tool_execution_start", tool_name="bash"),
        tmp_path,
    )

    [frame] = writes
    assert frame["type"] == "swarm_event"
    assert frame["swarm"] is True
    assert frame["project"] == str(find_project_root(tmp_path))
    assert frame["agent"] == "research"
    assert frame["task"] == "t_1"
    assert frame["event"]["type"] == "tool_execution_start"
    assert frame["event"]["tool_name"] == "bash"
    # routed by project, never by session: the desktop filters on ``project``
    assert "session" not in frame


async def test_the_manager_is_built_with_the_event_sink_attached(tmp_path, monkeypatch):
    host, _writes = _host(tmp_path)
    built: list[dict] = []
    import zenagent.cli.rpc.ops_swarm as ops

    class Probe(ops.SwarmManager):  # type: ignore[misc]
        def __init__(self, **kwargs):
            built.append(kwargs)
            super().__init__(**kwargs)

        async def start(self) -> None:
            return None

    monkeypatch.setattr(ops, "SwarmManager", Probe)
    await host._swarm_manager(tmp_path)
    assert built and built[0]["event_sink"] is not None
    host._swarm.clear()  # keep this test from closing a half-built manager


async def test_swarm_op_routes_by_project(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    _seed_role(a, "alpha")
    _seed_role(b, "beta")
    host, writes = _host(tmp_path)
    try:
        await host._swarm_op(
            {"type": "swarm", "op": "list", "project": str(a)}, "s1"
        )
        await host._swarm_op(
            {"type": "swarm", "op": "list", "project": str(b)}, "s2"
        )

        assert set(host._swarm) == {project_key(a), project_key(b)}
        assert {frozenset(manager.role_ids) for manager in host._swarm.values()} == {
            frozenset({"alpha"}),
            frozenset({"beta"}),
        }
        assert writes[-1]["type"] == "result" and writes[-1]["ok"] is True
    finally:
        await host.close()


async def test_idle_room_cap_evicts_only_idle_rooms(tmp_path):
    host, _writes = _host(tmp_path, max_slots=2)
    a, b, c, d = (tmp_path / name for name in "abcd")
    for root, role in ((a, "alpha"), (b, "beta"), (c, "gamma"), (d, "delta")):
        _seed_role(root, role)
    manager_a = manager_c = None
    try:
        manager_a = await host._swarm_manager(a)
        await host._swarm_manager(b)
        manager_a._running["alpha"] = object()  # a live run: never evicted

        manager_c = await host._swarm_manager(c)
        # B was the oldest idle room; A's live run kept it resident.
        assert set(host._swarm) == {project_key(a), project_key(c)}

        manager_c._running["gamma"] = object()
        await host._swarm_manager(d)
        # Every resident room is busy: overshoot rather than kill a run.
        assert set(host._swarm) == {
            project_key(a),
            project_key(c),
            project_key(d),
        }
        assert manager_a.has_active() and manager_c.has_active()
    finally:
        # Sentinels stand in for run tasks; clear them so close() can gather.
        if manager_a is not None:
            manager_a._running.clear()
        if manager_c is not None:
            manager_c._running.clear()
        await host.close()


async def test_project_close_tears_down_its_swarm_room(tmp_path):
    project = tmp_path / "proj"
    _seed_role(project, "alpha")
    host, writes = _host(project)
    manager = await host._swarm_manager(project)
    assert project_key(project) in host._swarm

    await _call(
        host,
        {"type": "project", "op": "close", "root": str(project), "id": "p1"},
    )

    assert project_key(project) not in host._swarm
    assert manager.started is False
    assert writes[-1]["type"] == "result" and writes[-1]["ok"] is True


async def test_roles_library_lists_global_roles_and_copy_lands_in_the_project(tmp_path):
    library = agents_root() / "research"
    library.mkdir(parents=True)
    (library / "manifest.json").write_text(
        json.dumps({"id": "research", "name": "Library Research"}), encoding="utf-8"
    )
    (library / "prompt.md").write_text("be nice", encoding="utf-8")
    project = tmp_path / "proj"
    project.mkdir()
    host, writes = _host(project)
    try:
        await _call(
            host, {"type": "swarm", "op": "roles", "action": "library", "id": "l1"}
        )
        assert writes[-1]["ok"] is True
        assert [role["id"] for role in writes[-1]["roles"]] == ["research"]

        await _call(
            host,
            {
                "type": "swarm",
                "op": "roles",
                "action": "copy",
                "id": "c1",
                "roleId": "research",
            },
        )
        assert writes[-1]["ok"] is True
        assert [role["id"] for role in writes[-1]["roles"]] == ["research"]
        copied = project / ".zenagent" / "agents" / "research"
        assert (copied / "prompt.md").read_text() == "be nice"
    finally:
        await host.close()


def _answer(writes: list[dict], request_id: str) -> dict:
    for frame in reversed(writes):
        if frame.get("id") == request_id and frame.get("type") in (
            "result",
            "error",
        ):
            return frame
    raise AssertionError(f"no answer for {request_id}")


async def test_works_list_detail_score_and_close(tmp_path, monkeypatch):
    from conftest import text_stream

    import zenagent.cli.rpc.ops_swarm as ops

    _seed_role(tmp_path, "research")

    class QuietManager(ops.SwarmManager):  # type: ignore[misc]
        def __init__(self, **kwargs):
            kwargs["stream_fn_factory"] = lambda role: text_stream("done")
            super().__init__(**kwargs)

    monkeypatch.setattr(ops, "SwarmManager", QuietManager)
    host, writes = _host(tmp_path)
    try:
        await _call(
            host,
            {
                "type": "swarm",
                "op": "post",
                "id": "s1",
                "text": "@research 查竞品",
                "project": str(tmp_path),
            },
        )
        await _call(
            host,
            {
                "type": "swarm",
                "op": "works",
                "id": "s2",
                "action": "list",
                "project": str(tmp_path),
            },
        )
        listed = _answer(writes, "s2")
        assert listed["ok"] is True
        assert len(listed["works"]) == 1
        work = listed["works"][0]
        assert work["state"] == "open"
        assert work["rootMessageId"]
        assert listed["adhoc"] == []

        await _call(
            host,
            {
                "type": "swarm",
                "op": "works",
                "id": "s3",
                "action": "score",
                "workId": work["id"],
                "score": 9,
                "project": str(tmp_path),
            },
        )
        bad = _answer(writes, "s3")
        assert bad["type"] == "error" and bad["code"] == "swarm_invalid"

        await _call(
            host,
            {
                "type": "swarm",
                "op": "works",
                "id": "s4",
                "action": "detail",
                "workId": work["id"],
                "project": str(tmp_path),
            },
        )
        detail = _answer(writes, "s4")
        assert detail["ok"] is True
        assert detail["work"]["id"] == work["id"]
        assert len(detail["rounds"]) == 1
        assert detail["rounds"][0]["workItemId"] == work["id"]

        await _call(
            host,
            {
                "type": "swarm",
                "op": "works",
                "id": "s5",
                "action": "close",
                "workId": work["id"],
                "score": 4,
                "note": "验收通过",
                "project": str(tmp_path),
            },
        )
        closed = _answer(writes, "s5")["work"]
        assert closed["state"] == "closed"
        assert closed["score"] == 4
        assert closed["closedAt"] is not None
    finally:
        await host.close()
