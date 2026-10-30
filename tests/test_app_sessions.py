"""SessionManager: listing, forking, trash deletion, scoping and legacy adoption."""

from pathlib import Path
from types import SimpleNamespace

import pytest

import zenagent.agent.session as session_module
from zenagent import project
from zenagent.agent.session import (
    Session,
    read_session_titles,
    session_title,
    set_session_title,
)
from zenagent.app.sessions import SessionManager, adopt_legacy_session
from zenagent.types import UserMessage


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


# ---------------------------------------------------------------------------
# Project scoping: entries(scope=...) and legacy adoption
# ---------------------------------------------------------------------------


def _enter_project(base: Path, monkeypatch, name: str) -> Path:
    """Turn ``base/name`` into a git repo and make it the current project."""
    root = base / name
    (root / ".git").mkdir(parents=True)
    monkeypatch.chdir(root)
    project.write_project_meta()
    return root


def _rebind_sessions_to_projects(monkeypatch) -> None:
    """Undo the app_env fixture's flat sessions dir: use per-project storage."""
    monkeypatch.setattr(
        session_module, "sessions_dir", lambda: project.project_sessions_dir()
    )


def _write_session(directory: Path, name: str, texts: list[str]) -> Path:
    path = directory / name
    session = Session(path)
    for index, text in enumerate(texts):
        session.append(UserMessage(content=text, timestamp=index))
    return path


def _legacy_session(
    base: Path, name: str = "old.jsonl", text: str = "legacy chat"
) -> Path:
    legacy = project.legacy_sessions_dir()
    legacy.mkdir(parents=True, exist_ok=True)
    return _write_session(legacy, name, [text])


def _manager_without_runtime() -> SessionManager:
    """``adopt`` never touches the Runtime; the CLI uses it with no API key."""
    return SessionManager(SimpleNamespace())


def test_adopt_legacy_session_moves_file_and_title(tmp_path, monkeypatch):
    source = _legacy_session(tmp_path)
    set_session_title(source, "Old saved name")
    _enter_project(tmp_path, monkeypatch, "alpha")
    target_dir = project.project_sessions_dir()

    adopted = adopt_legacy_session(source)

    assert adopted == target_dir / "old.jsonl"
    assert adopted.is_file()
    assert not source.exists()
    assert session_title(adopted) == "Old saved name"
    assert read_session_titles(target_dir) == {"old.jsonl": "Old saved name"}
    # Legacy data is read-only: adoption moves the title but not the old sidecar.
    assert read_session_titles(project.legacy_sessions_dir()) == {
        "old.jsonl": "Old saved name"
    }
    assert [
        message.content
        for message in Session(adopted).load()
        if isinstance(message, UserMessage)
    ] == ["legacy chat"]


def test_session_manager_adopt_uses_the_same_move(tmp_path, monkeypatch):
    source = _legacy_session(tmp_path)
    _enter_project(tmp_path, monkeypatch, "alpha")

    adopted = _manager_without_runtime().adopt(source)

    assert adopted == project.project_sessions_dir() / "old.jsonl"
    assert adopted.is_file() and not source.exists()


def test_adopt_legacy_session_resolves_an_explicit_cwd(tmp_path):
    source = _legacy_session(tmp_path)
    repo = tmp_path / "gamma"
    (repo / ".git").mkdir(parents=True)

    adopted = adopt_legacy_session(source, cwd=repo)

    assert adopted.parent == project.project_sessions_dir(cwd=repo)
    assert adopted.is_file() and not source.exists()


def test_adopt_never_overwrites_an_existing_project_session(tmp_path, monkeypatch):
    _enter_project(tmp_path, monkeypatch, "alpha")
    target_dir = project.project_sessions_dir()
    existing = _write_session(target_dir, "chat.jsonl", ["project chat"])
    set_session_title(existing, "Project name")
    source = _legacy_session(tmp_path, "chat.jsonl", "legacy chat")
    set_session_title(source, "Legacy name")

    adopted = adopt_legacy_session(source)

    assert adopted.name == "chat-1.jsonl"
    assert [
        message.content
        for message in Session(existing).load()
        if isinstance(message, UserMessage)
    ] == ["project chat"]
    assert session_title(existing) == "Project name"
    assert session_title(adopted) == "Legacy name"


def test_adopt_rejects_repeats_and_non_legacy_paths(tmp_path, monkeypatch):
    source = _legacy_session(tmp_path)
    _enter_project(tmp_path, monkeypatch, "alpha")
    adopted = adopt_legacy_session(source)

    with pytest.raises(ValueError):
        adopt_legacy_session(source)  # the legacy file is gone
    with pytest.raises(ValueError):
        adopt_legacy_session(adopted)  # already inside the current project
    stray = tmp_path / "stray.jsonl"
    stray.write_text("", encoding="utf-8")
    with pytest.raises(ValueError):
        adopt_legacy_session(stray)


async def test_entries_defaults_to_the_current_project(
    session_runtime, tmp_path, monkeypatch
):
    _enter_project(tmp_path, monkeypatch, "alpha")
    _rebind_sessions_to_projects(monkeypatch)
    manager = SessionManager(session_runtime)
    await manager.new()

    other = tmp_path / "beta"
    (other / ".git").mkdir(parents=True)
    project.write_project_meta(root=other)
    other_session = _write_session(
        project.project_sessions_dir(root=other), "other.jsonl", ["other chat"]
    )
    legacy_session = _legacy_session(tmp_path)

    entries = manager.entries()

    assert [entry.path for entry in entries] == [manager.current()]
    assert entries[0].current is True
    assert entries[0].project == "alpha"
    assert other_session.is_file() and legacy_session.is_file()  # merely not listed


async def test_entries_all_view_labels_each_project(
    session_runtime, tmp_path, monkeypatch
):
    _enter_project(tmp_path, monkeypatch, "alpha")
    _rebind_sessions_to_projects(monkeypatch)
    manager = SessionManager(session_runtime)
    current = await manager.new()

    other = tmp_path / "beta"
    (other / ".git").mkdir(parents=True)
    project.write_project_meta(root=other)
    other_session = _write_session(
        project.project_sessions_dir(root=other), "other.jsonl", ["other chat"]
    )
    legacy_session = _legacy_session(tmp_path)

    entries = manager.entries(scope="all")
    labels = {entry.path.name: entry.project for entry in entries}

    assert labels[other_session.name] == "beta"
    assert labels[legacy_session.name] == ""
    assert labels[current.name] == "alpha"
