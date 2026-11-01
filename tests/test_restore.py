"""``/restore``: shared command core formatting and registry wiring."""

from __future__ import annotations

from types import SimpleNamespace

from zenagent.app.commands import build_default_commands
from zenagent.app.journal import BackupPolicy, SnapshotJournal
from zenagent.app.restore import run_restore_command
from zenagent.types import TextContent, ToolCall


async def _seed_run(journal: SnapshotJournal, path, *, call_id: str, content="new"):
    journal.on_event(type("E", (), {"type": "agent_start"})())
    call = ToolCall(
        id=call_id, name="write_file", arguments={"path": str(path), "content": content}
    )
    await journal.before_tool_call(call, {"path": str(path), "content": content})
    path.write_text(content)
    from zenagent.agent.loop import ToolResultAgent

    await journal.after_tool_call(
        call, ToolResultAgent(content=[TextContent(text=content)])
    )


async def test_restore_latest_restores_the_run(tmp_path):
    journal = SnapshotJournal(tmp_path / "backups", BackupPolicy())
    target = tmp_path / "a.txt"
    target.write_text("old")
    await _seed_run(journal, target, call_id="c1")
    message = run_restore_command(journal, "", running=False)
    assert "restored" in message and str(target) in message
    assert target.read_text() == "old"


async def test_restore_list_shows_recent_runs(tmp_path):
    journal = SnapshotJournal(tmp_path / "backups", BackupPolicy())
    one = tmp_path / "one.txt"
    one.write_text("old")
    await _seed_run(journal, one, call_id="c1")
    message = run_restore_command(journal, "list", running=False)
    assert "1." in message and str(one) in message


async def test_restore_index_restores_older_run(tmp_path):
    journal = SnapshotJournal(tmp_path / "backups", BackupPolicy())
    one = tmp_path / "one.txt"
    one.write_text("old")
    await _seed_run(journal, one, call_id="c1")
    two = tmp_path / "two.txt"
    two.write_text("old")
    await _seed_run(journal, two, call_id="c2")
    message = run_restore_command(journal, "2", running=False)
    assert str(one) in message
    assert one.read_text() == "old"
    assert two.read_text() == "new"


def test_restore_without_journal_is_unavailable():
    assert "unavailable" in run_restore_command(None, "", running=False).lower()


def test_restore_while_running_is_refused(tmp_path):
    journal = SnapshotJournal(tmp_path / "backups", BackupPolicy())
    message = run_restore_command(journal, "", running=True)
    assert "running" in message.lower() or "cannot" in message.lower()


async def test_restore_with_no_runs_says_so(tmp_path):
    journal = SnapshotJournal(tmp_path / "backups", BackupPolicy())
    assert "nothing" in run_restore_command(journal, "", running=False).lower()


async def test_restore_bad_index_is_reported(tmp_path):
    journal = SnapshotJournal(tmp_path / "backups", BackupPolicy())
    target = tmp_path / "a.txt"
    target.write_text("old")
    await _seed_run(journal, target, call_id="c1")
    message = run_restore_command(journal, "9", running=False)
    assert "/restore list" in message


async def test_restore_is_registered_in_the_tui_registry(tmp_path):
    journal = SnapshotJournal(tmp_path / "backups", BackupPolicy())
    target = tmp_path / "a.txt"
    target.write_text("old")
    await _seed_run(journal, target, call_id="c1")
    services = SimpleNamespace(
        runtime=SimpleNamespace(journal=journal),
        chat=SimpleNamespace(status=SimpleNamespace(running=False)),
    )
    commands = build_default_commands(services)
    result = await commands.dispatch("restore", "")
    assert result.message and "restored" in result.message
