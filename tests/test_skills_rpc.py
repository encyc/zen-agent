"""The ``skills`` RPC kind: list/read/toggle/rescan."""

import json
from pathlib import Path

from zenagent import settings


def build_server(runtime, writes: list[str]):
    from zenagent.cli.rpc import RpcServer

    return RpcServer(runtime=runtime, write_line=writes.append)


async def call(server, writes: list[str], payload: dict) -> dict:
    payload.setdefault("id", "s1")
    assert await server._handle_line(json.dumps(payload))
    return json.loads(writes[-1])


def write_skill(root: Path, name: str, description: str, body: str = "") -> Path:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "SKILL.md"
    target.write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{body or 'Body.'}",
        encoding="utf-8",
    )
    return target


def roots(tmp_path: Path) -> list[Path]:
    return [tmp_path / "project-skills", tmp_path / "global-skills"]


async def test_list_reports_project_and_global_skills_with_enabled_state(
    runtime_factory, monkeypatch, tmp_path
):
    project_root, global_root = roots(tmp_path)
    write_skill(project_root, "deploy", "Ship the app")
    write_skill(global_root, "pdf", "Build a PDF")
    monkeypatch.setattr("zenagent.cli.rpc.ops_skills.default_skill_roots", lambda cwd: roots(tmp_path))
    runtime = await runtime_factory()
    writes: list[str] = []
    server = build_server(runtime, writes)

    result = await call(server, writes, {"type": "skills", "op": "list"})

    assert result["ok"] is True
    rows = {row["name"]: row for row in result["skills"]}
    assert set(rows) == {"deploy", "pdf"}
    assert rows["deploy"]["source"] == "project"
    assert rows["pdf"]["source"] == "global"
    assert rows["deploy"]["description"] == "Ship the app"
    assert rows["deploy"]["enabled"] is True

    settings.set_pref("disabled_skills", ["pdf"])
    disabled = await call(server, writes, {"type": "skills", "op": "list"})
    rows = {row["name"]: row for row in disabled["skills"]}
    assert rows["pdf"]["enabled"] is False
    assert rows["deploy"]["enabled"] is True


async def test_read_returns_the_body_and_rejects_unknown_skills(
    runtime_factory, monkeypatch, tmp_path
):
    write_skill(roots(tmp_path)[0], "deploy", "Ship the app", "Run `make ship`.\n")
    monkeypatch.setattr("zenagent.cli.rpc.ops_skills.default_skill_roots", lambda cwd: roots(tmp_path))
    runtime = await runtime_factory()
    writes: list[str] = []
    server = build_server(runtime, writes)

    result = await call(server, writes, {"type": "skills", "op": "read", "name": "deploy"})
    assert result["ok"] is True
    assert "Run `make ship`." in result["body"]
    assert result["path"].endswith("SKILL.md")

    unknown = await call(server, writes, {"type": "skills", "op": "read", "name": "ghost"})
    assert unknown["type"] == "error"
    assert "ghost" in unknown["message"]


async def test_toggle_persists_and_filters_the_live_runtime(
    runtime_factory, monkeypatch, tmp_path
):
    project_root, global_root = roots(tmp_path)
    write_skill(project_root, "deploy", "Ship the app")
    write_skill(global_root, "pdf", "Build a PDF")
    monkeypatch.setattr("zenagent.cli.rpc.ops_skills.default_skill_roots", lambda cwd: roots(tmp_path))
    monkeypatch.setattr(
        "zenagent.app.runtime.default_skill_roots", lambda cwd: roots(tmp_path)
    )
    monkeypatch.setattr(
        "zenagent.app.skills_recompose.default_skill_roots",
        lambda cwd: roots(tmp_path),
    )
    runtime = await runtime_factory(skills=True)
    assert {skill.name for skill in runtime.skills} == {"deploy", "pdf"}
    writes: list[str] = []
    server = build_server(runtime, writes)

    off = await call(
        server, writes, {"type": "skills", "op": "toggle", "name": "pdf", "enabled": False}
    )
    assert off["ok"] is True
    assert off["enabled"] is False
    assert settings.get_pref("disabled_skills") == ["pdf"]
    assert {skill.name for skill in runtime.skills} == {"deploy"}

    on = await call(
        server, writes, {"type": "skills", "op": "toggle", "name": "pdf", "enabled": True}
    )
    assert on["enabled"] is True
    assert settings.get_pref("disabled_skills") is None
    assert {skill.name for skill in runtime.skills} == {"deploy", "pdf"}


async def test_toggle_rejects_unknown_skills(runtime_factory, monkeypatch, tmp_path):
    write_skill(roots(tmp_path)[0], "deploy", "Ship the app")
    monkeypatch.setattr("zenagent.cli.rpc.ops_skills.default_skill_roots", lambda cwd: roots(tmp_path))
    runtime = await runtime_factory()
    writes: list[str] = []
    server = build_server(runtime, writes)

    result = await call(
        server, writes, {"type": "skills", "op": "toggle", "name": "ghost", "enabled": False}
    )

    assert result["type"] == "error"
    assert "ghost" in result["message"]


async def test_rescan_picks_up_a_skill_added_on_disk(
    runtime_factory, monkeypatch, tmp_path
):
    project_root, _ = roots(tmp_path)
    write_skill(project_root, "deploy", "Ship the app")
    monkeypatch.setattr("zenagent.cli.rpc.ops_skills.default_skill_roots", lambda cwd: roots(tmp_path))
    runtime = await runtime_factory()
    writes: list[str] = []
    server = build_server(runtime, writes)
    first = await call(server, writes, {"type": "skills", "op": "list"})
    assert [row["name"] for row in first["skills"]] == ["deploy"]

    write_skill(project_root, "review", "Review a change")
    second = await call(server, writes, {"type": "skills", "op": "rescan"})

    assert second["ok"] is True
    assert {row["name"] for row in second["skills"]} == {"deploy", "review"}


async def test_unknown_ops_are_rejected(runtime_factory):
    runtime = await runtime_factory()
    writes: list[str] = []
    server = build_server(runtime, writes)

    result = await call(server, writes, {"type": "skills", "op": "install"})

    assert result["type"] == "error"
    assert "install" in result["message"]
