"""Phase A acceptance: two projects, one process, no cwd races."""

import json

import pytest

from zenagent import project
from zenagent.app.agent_profile import AgentProfile
from zenagent.app.runtime import Runtime, RuntimeOptions
from zenagent.config import UserConfig


def _repo(tmp_path, name, note):
    root = tmp_path / name
    (root / ".git").mkdir(parents=True)
    (root / "note.txt").write_text(note, encoding="utf-8")
    return root


async def _bootstrap(monkeypatch, root):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(
        "zenagent.app.runtime.load_user_config",
        lambda *args, **kwargs: UserConfig(default_model="deepseek-flash"),
    )
    return await Runtime.bootstrap(
        RuntimeOptions(
            root=root,
            profile=AgentProfile(
                mcp=False, skills=False, templates=False, context_files=False
            ),
        )
    )


def _tool(runtime, name):
    return next(tool for tool in runtime._base_tools if tool.name == name)


@pytest.fixture
async def two_runtimes(monkeypatch, tmp_path):
    alpha = _repo(tmp_path, "alpha", "alpha note\n")
    beta = _repo(tmp_path, "beta", "beta note\n")
    a = await _bootstrap(monkeypatch, alpha)
    b = await _bootstrap(monkeypatch, beta)
    try:
        yield alpha, beta, a, b
    finally:
        await a.close()
        await b.close()


async def test_cwd_and_project_identity_are_per_runtime(two_runtimes):
    alpha, beta, a, b = two_runtimes

    assert a.cwd == alpha.resolve()
    assert b.cwd == beta.resolve()
    assert a.root == alpha.resolve()
    assert b.root == beta.resolve()
    assert a.gate.policy.workspace == alpha.resolve()
    assert b.gate.policy.workspace == beta.resolve()


async def test_sessions_do_not_cross_project_directories(two_runtimes):
    alpha, beta, a, b = two_runtimes

    assert a.session.path.parent == project.project_sessions_dir(root=alpha)
    assert b.session.path.parent == project.project_sessions_dir(root=beta)
    assert a.session.path != b.session.path

    a.session.materialize()
    assert b.session.path.exists() is False

    header = json.loads(a.session.path.read_text(encoding="utf-8").splitlines()[0])
    assert header["cwd"] == str(alpha.resolve())


async def test_memory_and_prompt_follow_the_runtime_root(two_runtimes):
    alpha, beta, a, b = two_runtimes

    assert a.memory_store.root == alpha.resolve()
    assert b.memory_store.root == beta.resolve()
    assert f"Working directory: {alpha.resolve()}" in a.agent.system_prompt
    assert f"Working directory: {beta.resolve()}" in b.agent.system_prompt


async def test_bound_tools_read_from_their_own_project(two_runtimes):
    _, _, a, b = two_runtimes

    alpha_read = await _tool(a, "read_file").execute(
        "c1", {"path": "note.txt"}, None, None
    )
    beta_read = await _tool(b, "read_file").execute(
        "c1", {"path": "note.txt"}, None, None
    )

    assert alpha_read.content[0].text == "alpha note\n"
    assert beta_read.content[0].text == "beta note\n"
