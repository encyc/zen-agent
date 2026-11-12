"""Phase B acceptance: two real projects, one host, no cross-project leaks."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from zenagent import project
from zenagent.app.bootstrap import prepare
from zenagent.app.runtime import RuntimeOptions
from zenagent.cli.rpc.host import RpcHost
from zenagent.cli.rpc.slot import RuntimeSlot
from zenagent.config import UserConfig

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git is not installed"
)


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _repo(tmp_path: Path, name: str) -> Path:
    root = tmp_path / name
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "phase-b@example.com")
    _git(root, "config", "user.name", "phase-b")
    (root / "note.txt").write_text(f"{name} note\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "init")
    return root


def _options(root: Path) -> RuntimeOptions:
    return RuntimeOptions(
        root=root,
        mcp=False,
        skills=False,
        templates=False,
        context_files=False,
        mode="ask",
    )


def _collect(writes: list[dict]):
    def write(line: str) -> None:
        writes.append(json.loads(line))

    return write


@pytest.fixture
async def host(monkeypatch, tmp_path):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    for target in (
        "zenagent.app.bootstrap.load_user_config",
        "zenagent.app.runtime.load_user_config",
    ):
        monkeypatch.setattr(
            target,
            lambda *args, **kwargs: UserConfig(default_model="deepseek-flash"),
        )
    alpha = _repo(tmp_path, "alpha")
    beta = _repo(tmp_path, "beta")
    gamma = _repo(tmp_path, "gamma")
    prepared = await prepare(_options(alpha), assume_yes=True)
    writes: list[dict] = []
    initial = RuntimeSlot.adopt(prepared, write_line=_collect(writes))
    instance = RpcHost(
        initial=initial,
        options=_options(alpha),
        read_line=None,
        write_line=_collect(writes),
        assume_yes=True,
        max_slots=2,
    )
    try:
        yield instance, writes, alpha, beta, gamma, tmp_path
    finally:
        await instance.close()


async def call(host: RpcHost, writes: list[dict], request: dict) -> dict:
    assert await host._handle_line(json.dumps(request))
    return writes[-1]


async def test_two_projects_share_one_host_without_leaking(host):
    instance, writes, alpha, beta, _, _ = host
    opened = await call(
        instance, writes, {"type": "project", "op": "open", "root": str(beta), "id": "o"}
    )
    assert opened["ok"] is True
    assert opened["active"] == str(beta.resolve())
    assert {item["key"] for item in opened["resident"]} == {
        project.project_key(alpha),
        project.project_key(beta),
    }

    alpha_slot = instance._slots[project.project_key(alpha)]
    beta_slot = instance._slots[project.project_key(beta)]
    assert alpha_slot.runtime.root == alpha.resolve()
    assert beta_slot.runtime.root == beta.resolve()
    assert alpha_slot.runtime.session.path.parent == project.project_sessions_dir(
        root=alpha
    )
    assert beta_slot.runtime.session.path.parent == project.project_sessions_dir(
        root=beta
    )

    alpha_tools = await call(
        instance,
        writes,
        {"type": "tools", "id": "t", "project": str(alpha)},
    )
    names = {tool["name"] for tool in alpha_tools["tools"]}
    assert "read_file" in names
    assert alpha_tools["project"] == str(alpha.resolve())


async def test_close_releases_the_project_and_relocate_then_succeeds(host):
    instance, writes, alpha, beta, _, tmp_path = host
    moved_root = tmp_path / "alpha2"
    moved_root.mkdir()
    await call(
        instance, writes, {"type": "project", "op": "open", "root": str(beta), "id": "o"}
    )

    refused = await call(
        instance,
        writes,
        {
            "type": "project",
            "op": "relocate",
            "from": str(alpha),
            "to": str(moved_root),
            "id": "r1",
        },
    )
    assert refused["code"] == "project_busy"

    closed = await call(
        instance,
        writes,
        {"type": "project", "op": "close", "root": str(alpha), "id": "c"},
    )
    assert closed["closed"] is True

    moved = await call(
        instance,
        writes,
        {
            "type": "project",
            "op": "relocate",
            "from": str(alpha),
            "to": str(moved_root),
            "id": "r2",
        },
    )
    assert moved["ok"] is True
    assert moved["old_key"] == project.project_key(alpha)
    assert moved["new_key"] == project.project_key(moved_root)
    assert moved["moved"] is True


async def test_lru_caps_residents(host):
    instance, writes, alpha, beta, gamma, _ = host
    await call(
        instance, writes, {"type": "project", "op": "open", "root": str(beta), "id": "b"}
    )
    await call(
        instance, writes, {"type": "project", "op": "open", "root": str(gamma), "id": "g"}
    )
    assert project.project_key(alpha) not in instance._slots
    assert project.project_key(beta) in instance._slots
    assert project.project_key(gamma) in instance._slots

async def test_zen_gives_the_router_its_own_slot(monkeypatch, tmp_path):
    """Entering zen must not touch the coding slot's session, run or tools."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    for target in (
        "zenagent.app.bootstrap.load_user_config",
        "zenagent.app.runtime.load_user_config",
    ):
        monkeypatch.setattr(
            target,
            lambda *args, **kwargs: UserConfig(default_model="deepseek-flash"),
        )
    alpha = _repo(tmp_path, "alpha")
    prepared = await prepare(_options(alpha), assume_yes=True)
    writes: list[dict] = []
    initial = RuntimeSlot.adopt(prepared, write_line=_collect(writes))
    instance = RpcHost(
        initial=initial,
        options=_options(alpha),
        read_line=None,
        write_line=_collect(writes),
        assume_yes=True,
        max_slots=4,
    )
    key = project.project_key(alpha)
    try:
        coding = instance._slots[key]
        coding_path = str(coding.runtime.session.path)

        frame = await call(
            instance, writes, {"type": "zen_fleet", "op": "enable", "id": "z1"}
        )

        assert frame["ok"] is True and frame["enabled"] is True
        assert str(coding.runtime.session.path) == coding_path
        router = instance._zen_slot_for(key)
        assert router is not None and router is not coding
        assert router.runtime.zen_fleet is not None
        assert instance.active is router
        assert frame["session"] == str(router.runtime.session.path)
        router_tools = {tool.name for tool in router.runtime.agent.tools}
        assert {"fleet_list", "fleet_dispatch", "fleet_spawn"} <= router_tools
        coding_tools = {tool.name for tool in coding.runtime.agent.tools}
        assert "fleet_dispatch" not in coding_tools
        assert Path(router.runtime.session.path).parent == (
            project.project_sessions_dir(root=alpha).resolve()
        )

        frame = await call(
            instance, writes, {"type": "zen_fleet", "op": "disable", "id": "z2"}
        )

        assert frame["enabled"] is False
        assert frame["session"] == coding_path
        assert instance.active is coding
        assert instance._zen_slot_for(key) is None
        assert len(instance._slots) == 1
        assert "fleet_dispatch" not in {tool.name for tool in coding.runtime.agent.tools}
    finally:
        await instance.close()


async def _wait_for(predicate, *, timeout_s: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not reached in time")


async def test_enabling_zen_does_not_abort_a_running_turn(monkeypatch, tmp_path):
    """The mode switch runs beside the coding run instead of killing it."""
    from zenagent.events import AssistantEvent
    from zenagent.types import AssistantMessage, TextContent, Usage

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    for target in (
        "zenagent.app.bootstrap.load_user_config",
        "zenagent.app.runtime.load_user_config",
    ):
        monkeypatch.setattr(
            target,
            lambda *args, **kwargs: UserConfig(default_model="deepseek-flash"),
        )
    gate = asyncio.Event()

    async def gated(model, messages, tools, options):
        partial = AssistantMessage(
            content=[TextContent(text="")],
            provider="p",
            model="m",
            usage=Usage(),
            stop_reason="pending",
            timestamp=0,
        )
        yield AssistantEvent(type="start", partial=partial)
        await gate.wait()
        partial.content[0].text = "done"
        partial.stop_reason = "stop"
        yield AssistantEvent(
            type="text_delta", partial=partial, content_index=0, delta="done"
        )
        yield AssistantEvent(type="done", partial=partial, reason="stop")

    alpha = _repo(tmp_path, "alpha")
    prepared = await prepare(_options(alpha), stream_fn=gated, assume_yes=True)
    writes: list[dict] = []
    initial = RuntimeSlot.adopt(prepared, write_line=_collect(writes))
    instance = RpcHost(
        initial=initial,
        options=_options(alpha),
        read_line=None,
        write_line=_collect(writes),
        assume_yes=True,
        stream_fn=gated,
        max_slots=4,
    )
    key = project.project_key(alpha)
    try:
        coding = instance._slots[key]
        coding_path = str(coding.runtime.session.path)
        # The prompt dispatches a run and returns; its answer is a stream of
        # event frames plus one result, so it is not a request/result call.
        await instance._handle_line(
            json.dumps({"type": "prompt", "id": "p1", "text": "go"})
        )
        await _wait_for(lambda: coding.runtime.agent.is_streaming)

        frame = await call(
            instance, writes, {"type": "zen_fleet", "op": "enable", "id": "z1"}
        )

        assert frame["ok"] is True and frame["enabled"] is True
        # The coding turn is still alive: same agent, same session, streaming.
        assert coding.runtime.agent.is_streaming is True
        assert coding.busy is True
        assert str(coding.runtime.session.path) == coding_path

        gate.set()
        await _wait_for(
            lambda: any(
                f.get("id") == "p1" and f.get("type") == "result" for f in writes
            )
        )
        result = next(
            f for f in writes if f.get("id") == "p1" and f.get("type") == "result"
        )
        assert result["ok"] is True
        assert result["termination"] == "stop"
        assert result["text"] == "done"
    finally:
        gate.set()
        await instance.close()
