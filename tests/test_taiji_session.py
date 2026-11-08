"""The per-project taiji session singleton and its host lock."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from zenagent.app.taiji import (
    TAIJI_SESSION_STEM,
    TaijiHostLocked,
    acquire_taiji_lock,
    ensure_taiji_session,
    is_taiji_session,
    own_taiji_lock,
    release_taiji_lock,
    taiji_session_path,
)


def test_session_path_uses_the_fixed_stem(tmp_path: Path) -> None:
    assert taiji_session_path(tmp_path) == tmp_path / f"{TAIJI_SESSION_STEM}.jsonl"
    assert TAIJI_SESSION_STEM == "taiji"


def test_ensure_creates_header_and_title_once(tmp_path: Path) -> None:
    first = ensure_taiji_session(tmp_path)
    assert first == taiji_session_path(tmp_path)
    lines = first.read_text(encoding="utf-8").splitlines()
    header = json.loads(lines[0])
    assert header["type"] == "session"
    assert header["id"] == "taiji"
    assert header["cwd"] == os.getcwd()
    titles = json.loads((tmp_path / ".titles.json").read_text(encoding="utf-8"))
    assert titles["taiji.jsonl"] == "太极 Router"

    second = ensure_taiji_session(tmp_path)
    assert second == first
    assert len(first.read_text(encoding="utf-8").splitlines()) == 1


def test_is_taiji_session_matches_only_the_fixed_stem(tmp_path: Path) -> None:
    assert is_taiji_session(tmp_path / "taiji.jsonl") is True
    assert is_taiji_session(tmp_path / "20260101-010101.jsonl") is False
    assert is_taiji_session(None) is False


def test_lock_acquires_blocks_and_covers(tmp_path: Path) -> None:
    acquire_taiji_lock(tmp_path)
    assert (tmp_path / "taiji.lock").exists()

    with pytest.raises(TaijiHostLocked):
        acquire_taiji_lock(tmp_path)

    # A lock left by a dead host (pid far above the pid ceiling) is stale.
    (tmp_path / "taiji.lock").write_text(json.dumps({"pid": 999999}), encoding="utf-8")
    acquire_taiji_lock(tmp_path)
    assert json.loads((tmp_path / "taiji.lock").read_text())["pid"] == os.getpid()


def test_release_removes_the_lock(tmp_path: Path) -> None:
    acquire_taiji_lock(tmp_path)
    release_taiji_lock(tmp_path)
    assert not (tmp_path / "taiji.lock").exists()
    release_taiji_lock(tmp_path)  # idempotent


def test_own_taiji_lock_never_touches_a_foreign_lock(tmp_path: Path) -> None:
    assert own_taiji_lock(tmp_path) is True
    assert json.loads((tmp_path / "taiji.lock").read_text())["pid"] == os.getpid()
    release_taiji_lock(tmp_path)

    (tmp_path / "taiji.lock").write_text(
        json.dumps({"pid": os.getpid()}), encoding="utf-8"
    )
    assert own_taiji_lock(tmp_path) is False
    # the rightful owner's lock file is untouched
    assert json.loads((tmp_path / "taiji.lock").read_text())["pid"] == os.getpid()
    assert own_taiji_lock(tmp_path) is False


def test_worker_worktree_hosts_do_not_materialize_taiji(tmp_path: Path) -> None:
    """A worker subprocess (cwd = a linked worktree) must not create taiji
    artifacts of its own — only the main checkout hosts taiji."""
    import shutil as _shutil
    import subprocess as _subprocess

    from zenagent.app.taiji.session import should_init_taiji

    if _shutil.which("git") is None:
        pytest.skip("git is not installed")
    repo = tmp_path / "repo"
    repo.mkdir()
    _subprocess.run(["git", "init", "-q", "-b", "main"], cwd=str(repo), check=True, capture_output=True)
    (repo / "a.txt").write_text("x\n", encoding="utf-8")
    _subprocess.run(["git", "add", "."], cwd=str(repo), check=True, capture_output=True)
    _subprocess.run(["git", "commit", "-qm", "init"], cwd=str(repo), check=True, capture_output=True)
    linked = tmp_path / "linked"
    _subprocess.run(["git", "worktree", "add", "-q", "-b", "w", str(linked)], cwd=str(repo), check=True, capture_output=True)

    assert should_init_taiji(repo) is True
    assert should_init_taiji(linked) is False
