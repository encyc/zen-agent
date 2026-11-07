"""The taiji end-to-end loop: spawn → work → milestone → merge, per mode."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_taiji_supervisor import FakeChat, StubRunner
from zenagent.app.taiji import close_taiji, ensure_taiji_session, get_taiji


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=False
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "taiji@example.com")
    _git(repo, "config", "user.name", "taiji")
    (repo / "a.txt").write_text("one\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "init")
    return repo


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
async def test_zen_mode_runs_the_full_autonomous_loop(
    repo: Path, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "zenagent.app.taiji.supervisor.worktrees_root", lambda: tmp_path / "home"
    )
    sessions_dir = tmp_path / "project" / "sessions"
    chat = FakeChat()

    session = ensure_taiji_session(sessions_dir)
    assert session.name == "taiji.jsonl"

    supervisor = get_taiji(
        repo_root=repo,
        sessions_dir=sessions_dir,
        registry_path=sessions_dir.parent / "taiji.json",
        runner=StubRunner(summary="added README"),
        chat=chat,
        mode_of=lambda: "zen",
    )
    try:
        await supervisor.spawn("add a README", name="readme")
        await supervisor.wait_idle()
    finally:
        await close_taiji()

    assert _git(repo, "rev-parse", "--verify", "refs/heads/task/readme").returncode == 0
    main_log = _git(repo, "log", "--format=%s", "main").stdout
    assert "work: add a README" in main_log
    assert not (tmp_path / "home" / "readme").exists()

    assert len(chat.messages) == 1
    assert chat.messages[0].startswith("[worker task/readme · merged]")
    assert "added README" in chat.messages[0]

    registry = dict(_load_registry(sessions_dir.parent / "taiji.json"))
    assert registry["readme"]["status"] == "merged"


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
async def test_ask_mode_stops_at_the_acceptance_question(
    repo: Path, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "zenagent.app.taiji.supervisor.worktrees_root", lambda: tmp_path / "home"
    )
    sessions_dir = tmp_path / "project" / "sessions"
    chat = FakeChat()

    ensure_taiji_session(sessions_dir)
    supervisor = get_taiji(
        repo_root=repo,
        sessions_dir=sessions_dir,
        registry_path=sessions_dir.parent / "taiji.json",
        runner=StubRunner(summary="draft ready"),
        chat=chat,
        mode_of=lambda: "ask",
    )
    try:
        await supervisor.spawn("draft the e2e", name="e2e")
        await supervisor.wait_idle()
    finally:
        await close_taiji()

    main_tip = _git(repo, "rev-parse", "main").stdout.strip()
    assert (
        _git(repo, "merge-base", "--is-ancestor", "task/e2e", main_tip).returncode != 0
    )
    assert len(chat.messages) == 2
    assert chat.messages[0].startswith("[worker task/e2e · ok]")
    assert "待验收" in chat.messages[1]
    registry = dict(_load_registry(sessions_dir.parent / "taiji.json"))
    assert registry["e2e"]["status"] == "ok"


def _load_registry(path: Path) -> dict:
    import json

    return json.loads(path.read_text(encoding="utf-8"))
