"""The zen roster sidecar: which project sessions are router or worker."""

from __future__ import annotations

from pathlib import Path

from zenagent.app.zen.roster import RosterEntry, latest_router, read_roster, set_focus, set_role


def test_role_and_focus_roundtrip(tmp_path: Path) -> None:
    set_role(tmp_path, "s1", "router")
    set_focus(tmp_path, "s1", "编译修复")
    roster = read_roster(tmp_path)
    assert roster["s1"] == RosterEntry(role="router", focus="编译修复")


def test_focus_on_unknown_session_creates_worker(tmp_path: Path) -> None:
    set_focus(tmp_path, "w1", "文档巡检")
    roster = read_roster(tmp_path)
    assert roster["w1"] == RosterEntry(role="worker", focus="文档巡检")


def test_role_change_keeps_focus(tmp_path: Path) -> None:
    set_focus(tmp_path, "s1", "搜索优化")
    set_role(tmp_path, "s1", "router")
    assert read_roster(tmp_path)["s1"] == RosterEntry(role="router", focus="搜索优化")


def test_latest_router_prefers_newest_write(tmp_path: Path) -> None:
    set_role(tmp_path, "a", "router")
    set_role(tmp_path, "b", "worker")
    set_role(tmp_path, "c", "router")
    assert latest_router(tmp_path) == "c"


def test_latest_router_none_without_router(tmp_path: Path) -> None:
    set_role(tmp_path, "a", "worker")
    assert latest_router(tmp_path) is None


def test_missing_and_corrupt_files_are_empty(tmp_path: Path) -> None:
    assert read_roster(tmp_path) == {}
    (tmp_path / ".zen.json").write_text("{oops", encoding="utf-8")
    assert read_roster(tmp_path) == {}
    assert latest_router(tmp_path) is None
