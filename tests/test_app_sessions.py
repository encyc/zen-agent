"""SessionManager: listing, forking and trash-based deletion."""

import pytest

from zenagent.app.sessions import SessionManager


async def test_fork_copies_messages_and_switches(session_runtime):
    runtime = session_runtime
    manager = SessionManager(runtime)
    original = manager.current()
    await runtime.agent.prompt("hello")

    forked = await manager.fork()

    assert forked != original and forked.is_file()
    assert manager.current() == forked
    assert len(runtime.agent.messages) == 2  # user + assistant copied over


async def test_delete_moves_to_trash_instead_of_unlink(session_runtime):
    runtime = session_runtime
    manager = SessionManager(runtime)
    path = manager.current()

    trashed = manager.delete(path)

    assert not path.exists()
    assert trashed.exists() and trashed.parent.name == ".trash"


async def test_rename_updates_the_listed_title(session_runtime):
    runtime = session_runtime
    manager = SessionManager(runtime)
    await runtime.agent.prompt("hello there")
    path = manager.current()

    stored = manager.rename(path, "  Renamed   chat ")

    assert stored == "Renamed chat"
    entry = next(entry for entry in manager.entries() if entry.path == path)
    assert entry.title == "Renamed chat"


async def test_rename_rejects_a_missing_session_path(session_runtime, tmp_path):
    manager = SessionManager(session_runtime)

    with pytest.raises(ValueError):
        manager.rename(tmp_path / "does-not-exist.jsonl", "name")


async def test_rename_rejects_a_blank_title(session_runtime):
    manager = SessionManager(session_runtime)
    path = manager.current()

    with pytest.raises(ValueError):
        manager.rename(path, "   ")


async def test_fork_gets_a_derived_title_not_the_old_name(session_runtime):
    runtime = session_runtime
    manager = SessionManager(runtime)
    await runtime.agent.prompt("hello")
    original = manager.current()
    manager.rename(original, "Custom name")

    forked = await manager.fork()

    entry = next(entry for entry in manager.entries() if entry.path == forked)
    assert entry.title == "hello"  # derived from the copied first user message
