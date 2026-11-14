"""Snapshot journal: project-scoped pre-write captures, restore, retention."""

from __future__ import annotations

import stat

from zenagent import project
from zenagent.agent.loop import ToolResultAgent
from zenagent.app.journal import (
    BackupPolicy,
    SnapshotJournal,
    attach_journal,
    journal_for,
    load_backup_policy,
)
from zenagent.config import UserConfig
from zenagent.types import TextContent, ToolCall


def _append_garbage(path) -> None:
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("not json at all\n")


def _ok(text: str = "ok") -> ToolResultAgent:
    return ToolResultAgent(content=[TextContent(text=text)])


def _error(text: str = "boom") -> ToolResultAgent:
    return ToolResultAgent(content=[TextContent(text=text)], is_error=True)


def _start(journal: SnapshotJournal) -> None:
    journal.on_event(type("E", (), {"type": "agent_start"})())


def _turn(journal: SnapshotJournal) -> None:
    journal.on_event(type("E", (), {"type": "turn_start"})())


async def _capture(journal: SnapshotJournal, path, call_id="c1", content="new"):
    call = ToolCall(
        id=call_id, name="write_file", arguments={"path": str(path), "content": content}
    )
    await journal.before_tool_call(call, {"path": str(path), "content": content})
    return call


async def test_capture_commit_and_restore(tmp_path):
    journal = SnapshotJournal(tmp_path / "backups", BackupPolicy())
    _start(journal)
    path = tmp_path / "a.txt"
    path.write_text("old")
    call = await _capture(journal, path)
    path.write_text("new")
    assert await journal.after_tool_call(call, _ok("new")) is None
    runs = journal.runs()
    assert len(runs) == 1 and runs[0].files == 1

    path.write_text("user edit")
    report = journal.restore(runs[0].run)
    assert report.skipped and not report.restored
    assert path.read_text() == "user edit"

    path.write_text("new")
    report = journal.restore(runs[0].run)
    assert report.restored == [str(path)]
    assert path.read_text() == "old"


async def test_created_file_is_deleted_on_restore(tmp_path):
    journal = SnapshotJournal(tmp_path / "backups", BackupPolicy())
    _start(journal)
    path = tmp_path / "new.txt"
    call = await _capture(journal, path)
    path.write_text("x")
    await journal.after_tool_call(call, _ok("x"))
    journal.restore(journal.runs()[0].run)
    assert not path.exists()


async def test_edit_file_is_captured(tmp_path):
    journal = SnapshotJournal(tmp_path / "backups", BackupPolicy())
    _start(journal)
    path = tmp_path / "a.txt"
    path.write_text("one\ntwo\n")
    call = ToolCall(
        id="e1",
        name="edit_file",
        arguments={"path": str(path), "old_string": "two", "new_string": "three"},
    )
    await journal.before_tool_call(call, {"path": str(path)})
    path.write_text("one\nthree\n")
    await journal.after_tool_call(call, _ok("edited"))
    journal.restore(journal.runs()[0].run)
    assert path.read_text() == "one\ntwo\n"


async def test_too_large_is_skipped_and_reported(tmp_path):
    journal = SnapshotJournal(tmp_path / "backups", BackupPolicy(max_file_bytes=4))
    _start(journal)
    path = tmp_path / "big.bin"
    path.write_bytes(b"x" * 10)
    call = await _capture(journal, path)
    path.write_bytes(b"new")
    result = await journal.after_tool_call(call, _ok("new"))
    assert result is not None
    assert "backup skipped" in " ".join(block.text for block in result.content)
    report = journal.restore(journal.runs()[0].run)
    assert report.skipped and not report.restored


async def test_error_result_drops_the_snapshot(tmp_path):
    journal = SnapshotJournal(tmp_path / "backups", BackupPolicy())
    _start(journal)
    path = tmp_path / "a.txt"
    path.write_text("old")
    call = await _capture(journal, path)
    assert await journal.after_tool_call(call, _error()) is None
    assert journal.runs() == []
    assert not (tmp_path / "backups" / "manifest.jsonl").exists()


async def test_parallel_writes_restore_pre_run_state(tmp_path):
    journal = SnapshotJournal(tmp_path / "backups", BackupPolicy())
    _start(journal)
    path = tmp_path / "a.txt"
    path.write_text("old")
    first = await _capture(journal, path, call_id="c1")
    second = await _capture(journal, path, call_id="c2")
    path.write_text("one")
    await journal.after_tool_call(first, _ok("one"))
    path.write_text("two")
    await journal.after_tool_call(second, _ok("two"))
    journal.restore(journal.runs()[0].run)
    assert path.read_text() == "old"


async def test_runs_group_by_run_newest_first(tmp_path):
    journal = SnapshotJournal(tmp_path / "backups", BackupPolicy())
    _start(journal)
    _turn(journal)
    one = tmp_path / "one.txt"
    one.write_text("old")
    await journal.after_tool_call(await _capture(journal, one, "c1"), _ok())
    _start(journal)
    two = tmp_path / "two.txt"
    two.write_text("old")
    await journal.after_tool_call(await _capture(journal, two, "c2"), _ok())
    runs = journal.runs()
    assert [run.files for run in runs] == [1, 1]
    assert runs[0].paths == (str(two),)
    assert runs[1].paths == (str(one),)


async def test_manifest_skips_corrupt_lines(tmp_path):
    journal = SnapshotJournal(tmp_path / "backups", BackupPolicy())
    _start(journal)
    path = tmp_path / "a.txt"
    path.write_text("old")
    await journal.after_tool_call(await _capture(journal, path), _ok())
    _append_garbage(journal.manifest_path)
    assert len(journal.runs()) == 1


async def test_restore_missing_blob_is_skipped(tmp_path):
    journal = SnapshotJournal(tmp_path / "backups", BackupPolicy())
    _start(journal)
    path = tmp_path / "a.txt"
    path.write_text("old")
    await journal.after_tool_call(await _capture(journal, path), _ok())
    for blob in (tmp_path / "backups" / "blobs").glob("*/*"):
        blob.unlink()
    report = journal.restore(journal.runs()[0].run)
    assert report.skipped and report.skipped[0][1] == "blob missing"


async def test_restore_preserves_existing_mode(tmp_path):
    journal = SnapshotJournal(tmp_path / "backups", BackupPolicy())
    _start(journal)
    path = tmp_path / "run.sh"
    path.write_text("#!/bin/sh\necho old\n")
    path.chmod(0o755)
    await journal.after_tool_call(await _capture(journal, path), _ok())
    journal.restore(journal.runs()[0].run)
    assert stat.S_IMODE(path.stat().st_mode) == 0o755


async def test_storage_permissions(tmp_path):
    journal = SnapshotJournal(tmp_path / "backups", BackupPolicy())
    _start(journal)
    path = tmp_path / "a.txt"
    path.write_text("old")
    await journal.after_tool_call(await _capture(journal, path), _ok())
    assert stat.S_IMODE((tmp_path / "backups").stat().st_mode) == 0o700
    assert stat.S_IMODE(journal.manifest_path.stat().st_mode) == 0o600
    blobs = list((tmp_path / "backups" / "blobs").glob("*/*"))
    assert blobs and all(stat.S_IMODE(blob.stat().st_mode) == 0o600 for blob in blobs)


async def test_prune_keeps_last_runs_and_deletes_orphan_blobs(tmp_path):
    backups = tmp_path / "backups"
    journal = SnapshotJournal(backups, BackupPolicy())
    _start(journal)
    first = tmp_path / "first.txt"
    first.write_text("old")
    await journal.after_tool_call(await _capture(journal, first, "c1"), _ok())
    _start(journal)
    second = tmp_path / "second.txt"
    second.write_text("old")
    await journal.after_tool_call(await _capture(journal, second, "c2"), _ok())
    assert len(journal.runs()) == 2

    pruned = SnapshotJournal(backups, BackupPolicy(keep_runs=1))
    runs = pruned.runs()
    assert len(runs) == 1 and runs[0].paths == (str(second),)
    referenced = {line["blob"] for line in pruned._read_manifest() if line.get("blob")}
    for blob in (backups / "blobs").glob("*/*"):
        assert blob.name in referenced


async def test_restore_unknown_run_reports_it(tmp_path):
    journal = SnapshotJournal(tmp_path / "backups", BackupPolicy())
    report = journal.restore("does-not-exist")
    assert report.run is None
    assert report.skipped == [("does-not-exist", "unknown run")]


def test_journal_for_respects_disabled_policy(tmp_path):
    assert journal_for(None, BackupPolicy(enabled=False), cwd=tmp_path) is None
    journal = journal_for(None, BackupPolicy(), cwd=tmp_path)
    assert journal is not None
    assert journal.backups_dir == project.project_backups_dir(tmp_path)


def test_load_backup_policy_from_user_config():
    policy = load_backup_policy(
        UserConfig(
            backup_enabled=False,
            backup_max_file_bytes=4096,
            backup_keep_runs=7,
        )
    )
    assert policy == BackupPolicy(enabled=False, max_file_bytes=4096, keep_runs=7)


async def test_attach_journal_composes_and_block_wins(tmp_path):
    journal = SnapshotJournal(tmp_path / "backups", BackupPolicy())
    _start(journal)

    class FakeAgent:
        def __init__(self) -> None:
            self.before_tool_call = self._hook
            self.after_tool_call = None
            self.subscribed = []

        async def _hook(self, call, args, tool=None):
            return {"block": True, "reason": "nope"} if args.get("block") else None

        def subscribe(self, callback):
            self.subscribed.append(callback)

    agent = FakeAgent()
    attach_journal(agent, journal)
    path = tmp_path / "a.txt"
    path.write_text("old")

    blocked_call = ToolCall(id="b1", name="write_file", arguments={"path": str(path)})
    assert await agent.before_tool_call(
        blocked_call, {"path": str(path), "block": True}
    ) == {"block": True, "reason": "nope"}
    assert journal.runs() == []

    allowed = ToolCall(id="c1", name="write_file", arguments={"path": str(path)})
    assert await agent.before_tool_call(allowed, {"path": str(path)}) is None
    path.write_text("new")
    assert agent.after_tool_call is not None
    await agent.after_tool_call(allowed, _ok())
    assert len(journal.runs()) == 1
    assert agent.subscribed == [journal.on_event]


async def test_attach_journal_with_two_arg_hook(tmp_path):
    journal = SnapshotJournal(tmp_path / "backups", BackupPolicy())

    class FakeAgent:
        def __init__(self) -> None:
            self.before_tool_call = lambda call, args: None
            self.after_tool_call = None

        def subscribe(self, callback):
            pass

    path = tmp_path / "a.txt"
    path.write_text("old")
    call = ToolCall(id="c1", name="write_file", arguments={"path": str(path)})
    # the composed hook accepts a 2-arg predecessor hook:
    agent = FakeAgent()
    attach_journal(agent, journal)
    assert await agent.before_tool_call(call, {"path": str(path)}) is None


def test_project_backups_dir(tmp_path):
    assert project.project_backups_dir(tmp_path) == project.project_dir(tmp_path) / "backups"


async def test_restore_survives_a_new_session_file(tmp_path):
    backups = tmp_path / "backups"
    journal = SnapshotJournal(backups, BackupPolicy(), session="s1")
    _start(journal)
    path = tmp_path / "a.txt"
    path.write_text("old")
    await journal.after_tool_call(await _capture(journal, path), _ok())

    # /undo, /compact and /fork all point the runtime at a fresh session file;
    # the project-scoped log must still see the earlier run.
    rewritten = SnapshotJournal(backups, BackupPolicy(), session="s2")
    runs = rewritten.runs()
    assert runs and runs[0].session == "s1"
    report = rewritten.restore(runs[0].run)
    assert report.restored == [str(path)]
    assert path.read_text() == "old"


def test_blob_and_manifest_writes_fsync(tmp_path, monkeypatch):
    """P2: a rename before its bytes hit disk loses the file on power cut."""
    import os as os_mod

    journal = SnapshotJournal(tmp_path / "backups", BackupPolicy())
    fsyncs = []
    real_fsync = os_mod.fsync

    def counting_fsync(fd):
        fsyncs.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(os_mod, "fsync", counting_fsync)

    journal._write_blob("a" * 64, b"payload")
    journal._rewrite_manifest([{"id": 1}])
    assert fsyncs, "blob and manifest writes must fsync before os.replace"
    assert len(fsyncs) >= 2
