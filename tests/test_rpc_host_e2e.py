"""Phase B acceptance: two real projects, one host, no cross-project leaks."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from zenagent import project
from zenagent.app.bootstrap import prepare
from zenagent.app.runtime import RuntimeOptions
from zenagent.app.taiji import existing_taiji
from zenagent.app.taiji.supervisor import _git
from zenagent.cli.rpc.host import RpcHost
from zenagent.cli.rpc.slot import RuntimeSlot
from zenagent.config import UserConfig

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git is not installed"
)


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

    assert existing_taiji(alpha) is not None
    assert existing_taiji(beta) is not None
    assert existing_taiji(alpha) is not existing_taiji(beta)


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
    assert existing_taiji(alpha) is None
    assert not (
        project.project_dir(root=alpha) / "taiji.lock"
    ).exists()

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


async def test_lru_caps_residents_and_releases_the_evicted_taiji(host):
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
    assert existing_taiji(alpha) is None