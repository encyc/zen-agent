"""The taiji worker registry: persistence and crash reconciliation."""

from __future__ import annotations

import json
from pathlib import Path

from zenagent.app.taiji.registry import TaijiRegistry, WorkerRecord


def make_record(name: str = "login-timeout", status: str = "running") -> WorkerRecord:
    return WorkerRecord(
        name=name,
        branch=f"task/{name}",
        base="main",
        worktree=f"/wt/{name}",
        status=status,
        created_at=1.0,
        updated_at=2.0,
    )


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    registry = TaijiRegistry()
    registry.records["login-timeout"] = make_record()
    registry.records["e2e"] = make_record("e2e", status="ok")
    registry.save(tmp_path / "taiji.json")

    loaded = TaijiRegistry()
    loaded.load(tmp_path / "taiji.json")

    assert set(loaded.records) == {"login-timeout", "e2e"}
    assert loaded.records["login-timeout"].branch == "task/login-timeout"
    assert loaded.records["e2e"].status == "ok"


def test_load_of_missing_or_broken_file_yields_empty(tmp_path: Path) -> None:
    registry = TaijiRegistry()
    registry.load(tmp_path / "missing.json")
    assert registry.records == {}

    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    registry.load(tmp_path / "broken.json")
    assert registry.records == {}


def test_reconcile_marks_running_as_interrupted_and_removes_gone_worktrees(
    tmp_path: Path,
) -> None:
    registry = TaijiRegistry()
    registry.records["live"] = make_record("live", status="running")
    registry.records["gone"] = make_record("gone", status="ok")
    registry.records["dead"] = make_record("dead", status="running")

    registry.reconcile(worktree_paths=["/wt/live"])

    assert registry.records["live"].status == "interrupted"
    assert "gone" not in registry.records  # worktree removed, terminal status: drop
    assert registry.records["dead"].status == "interrupted"


def test_save_is_atomic(tmp_path: Path) -> None:
    registry = TaijiRegistry()
    registry.records["a"] = make_record("a")
    target = tmp_path / "taiji.json"
    registry.save(target)
    assert json.loads(target.read_text(encoding="utf-8"))["a"]["status"] == "running"
    assert not list(tmp_path.glob("*.tmp"))


def test_registry_roundtrips_prompt_and_inbox(tmp_path: Path) -> None:
    """A crash before the first turn must not lose the task itself: the
    prompt persists, and queued interjections survive the restart."""
    registry = TaijiRegistry()
    record = make_record("survivor")
    record.prompt = "fix the flaky login test"
    record.inbox = ["also check the logs"]
    registry.records["survivor"] = record
    target = tmp_path / "taiji.json"
    registry.save(target)

    reloaded = TaijiRegistry()
    reloaded.load(target)
    assert reloaded.records["survivor"].prompt == "fix the flaky login test"
    assert reloaded.records["survivor"].inbox == ["also check the logs"]
