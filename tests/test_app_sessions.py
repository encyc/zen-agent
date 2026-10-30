"""SessionManager: listing, forking and trash-based deletion."""

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
