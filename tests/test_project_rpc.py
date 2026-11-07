"""The ``project`` RPC kind: relocating a project's state to a renamed folder."""

import json

from zenagent import project


def build_server(runtime, writes: list[str]):
    from zenagent.cli.rpc import RpcServer

    return RpcServer(runtime=runtime, write_line=writes.append)


async def call(server, writes: list[str], payload: dict) -> dict:
    payload.setdefault("id", "p1")
    assert await server._handle_line(json.dumps(payload))
    return json.loads(writes[-1])


def make_repo(path):
    (path / ".git").mkdir(parents=True, exist_ok=True)
    return path


def seed_project(root, sessions: int = 1) -> None:
    project.write_project_meta(root=root)
    directory = project.project_sessions_dir(root=root)
    directory.mkdir(parents=True, exist_ok=True)
    for index in range(sessions):
        (directory / f"2026010{index}-000000.jsonl").write_text("{}\n")


async def test_relocate_rpc_moves_a_project_that_is_not_open(
    runtime_factory, tmp_path
):
    runtime = await runtime_factory()
    old = make_repo(tmp_path / "old-name")
    new = make_repo(tmp_path / "new-name")
    seed_project(old, sessions=2)
    writes: list[str] = []
    server = build_server(runtime, writes)

    result = await call(
        server,
        writes,
        {"type": "project", "op": "relocate", "from": str(old), "to": str(new)},
    )

    assert result["ok"] is True
    assert result["moved"] is True
    assert result["sessions"] == 2
    assert result["new_root"] == str(new.resolve())
    assert not project.project_dir(root=old).exists()
    moved_meta = project.load_project_meta(root=new)
    assert moved_meta is not None
    assert moved_meta.root == str(new.resolve())


async def test_relocate_rpc_refuses_the_project_that_is_open(
    runtime_factory, tmp_path, monkeypatch
):
    open_repo = make_repo(tmp_path / "open-name")
    seed_project(open_repo, sessions=0)
    monkeypatch.setattr(
        "zenagent.agent.session.sessions_dir",
        lambda: project.project_sessions_dir(root=open_repo),
    )
    runtime = await runtime_factory()
    open_root = project.project_root_for_sessions_dir(runtime.session.path.parent)
    assert open_root == str(open_repo.resolve())
    new = make_repo(tmp_path / "new-name")
    writes: list[str] = []
    server = build_server(runtime, writes)

    result = await call(
        server,
        writes,
        {
            "type": "project",
            "op": "relocate",
            "from": open_root,
            "to": str(new),
        },
    )

    assert result["type"] == "error"
    assert "switch" in result["message"]
    assert project.project_dir(root=open_root).is_dir()


async def test_relocate_rpc_rejects_missing_arguments(runtime_factory):
    runtime = await runtime_factory()
    writes: list[str] = []
    server = build_server(runtime, writes)

    result = await call(
        server, writes, {"type": "project", "op": "relocate", "from": "/tmp/x"}
    )

    assert result["type"] == "error"
    assert "to" in result["message"]
