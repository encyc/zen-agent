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
