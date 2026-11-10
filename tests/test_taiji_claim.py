"""The per-project taiji host claim: session, lock, fleet — and release."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from zenagent import project
from zenagent.app.taiji import (
    TaijiClaim,
    claim_taiji_host,
    ensure_taiji_session,
    release_taiji_host,
    taiji_session_path,
)
from zenagent.app.taiji.supervisor import _git, existing_taiji


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


class FakeRuntime:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.cwd = self.root
        self.mode = "ask"
        self.taiji_chat = None
        self.notes: list[str] = []

    def _note(self, text: str) -> None:
        self.notes.append(text)


def test_ensure_taiji_session_records_the_project_cwd(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    path = ensure_taiji_session(sessions, cwd=tmp_path / "proj")
    header = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert header["cwd"] == str((tmp_path / "proj").resolve())


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
async def test_claim_owns_the_host_and_release_clears_it(repo: Path) -> None:
    claim = claim_taiji_host(FakeRuntime(repo))
    try:
        assert claim == TaijiClaim(root=repo.resolve(), owned=True)
        assert existing_taiji(repo) is not None
        session = taiji_session_path(project.project_sessions_dir(root=repo))
        header = json.loads(session.read_text(encoding="utf-8").splitlines()[0])
        assert header["cwd"] == str(repo.resolve())
        assert (project.project_dir(root=repo) / "taiji.lock").exists()
    finally:
        await release_taiji_host(claim)
    assert existing_taiji(repo) is None
    assert not (project.project_dir(root=repo) / "taiji.lock").exists()


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
async def test_a_second_claim_on_the_same_project_is_not_owned(repo: Path) -> None:
    runtime = FakeRuntime(repo)
    first = claim_taiji_host(runtime)
    try:
        second = claim_taiji_host(runtime)
        assert second.owned is False
        assert runtime.notes  # the host told the person who owns the lock
    finally:
        await release_taiji_host(first)


async def test_claim_is_inert_outside_a_main_checkout(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "zenagent.app.taiji.claim.should_init_taiji", lambda root: False
    )
    runtime = FakeRuntime(repo)
    claim = claim_taiji_host(runtime)
    assert claim == TaijiClaim(root=repo.resolve(), owned=False)
    assert not taiji_session_path(
        project.project_sessions_dir(root=repo)
    ).exists()
    await release_taiji_host(claim)
