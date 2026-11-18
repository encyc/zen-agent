"""Listing index: ``read_info`` facts cached beside the sessions directory.

A listing reads every transcript to count messages and derive a title. The
cache stores those derived facts per file, revalidated by ``(size,
mtime_ns)``; rename overrides are applied fresh on every read, so renaming
never needs cache invalidation.
"""

import json

import pytest

import zenagent.agent.session as session_module
from zenagent.agent.session import Session, list_sessions, set_session_title
from zenagent.types import (
    AssistantMessage,
    TextContent,
    Usage,
    UserMessage,
)


@pytest.fixture
def sessions_root(tmp_path, monkeypatch):
    sessions_dir = tmp_path / "sessions"
    monkeypatch.setattr(
        session_module, "sessions_dir", lambda *, root=None: sessions_dir
    )
    sessions_dir.mkdir()
    return sessions_dir


def write_session(root, name, text="hello"):
    session = Session(root / name)
    session._append_sync(UserMessage(content=text, timestamp=1))
    session._append_sync(
        AssistantMessage(
            content=[TextContent(text="hi")],
            provider="p",
            model="test-model",
            usage=Usage(),
            stop_reason="stop",
            timestamp=2,
        )
    )
    return root / name


def count_read_info(monkeypatch) -> list[int]:
    calls = []

    original = Session.read_info

    def counting(self, **kwargs):
        calls.append(self.path.name)
        return original(self, **kwargs)

    monkeypatch.setattr(Session, "read_info", counting)
    return calls


def test_second_listing_hits_cache(sessions_root, monkeypatch):
    write_session(sessions_root, "a.jsonl", "first")
    write_session(sessions_root, "b.jsonl", "second")
    calls = count_read_info(monkeypatch)

    first = list_sessions()
    assert len(calls) == 2

    second = list_sessions()
    assert len(calls) == 2, "the second listing must not re-read any file"
    assert second == first
    assert sorted(info.title for info in second) == ["first", "second"]


def test_append_invalidates_entry(sessions_root, monkeypatch):
    write_session(sessions_root, "a.jsonl", "first")
    write_session(sessions_root, "b.jsonl", "second")
    calls = count_read_info(monkeypatch)
    list_sessions()

    session = Session(sessions_root / "a.jsonl")
    session._append_sync(UserMessage(content="more", timestamp=3))
    list_sessions()

    assert sorted(calls) == ["a.jsonl", "a.jsonl", "b.jsonl"], (
        "only the changed file is re-read"
    )
    by_name = {info.path.name: info for info in list_sessions()}
    assert by_name["a.jsonl"].messages == 3


def test_corrupt_index_tolerated(sessions_root, monkeypatch):
    write_session(sessions_root, "a.jsonl", "first")
    (sessions_root / "_index.json").write_text("{not json", encoding="utf-8")
    calls = count_read_info(monkeypatch)

    infos = list_sessions()

    assert [info.title for info in infos] == ["first"]
    assert len(calls) == 1
    # A valid index replaces the corrupt one.
    assert json.loads((sessions_root / "_index.json").read_text())["a.jsonl"]


def test_flush_failure_swallowed(sessions_root, monkeypatch):
    write_session(sessions_root, "a.jsonl", "first")

    import zenagent.agent.session_index as index_module

    def broken_replace(src, dst):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(index_module.os, "replace", broken_replace)

    infos = list_sessions()

    assert [info.title for info in infos] == ["first"]


def test_titles_override_applied_fresh(sessions_root, monkeypatch):
    write_session(sessions_root, "a.jsonl", "first")
    calls = count_read_info(monkeypatch)
    assert list_sessions()[0].title == "first"

    set_session_title(sessions_root / "a.jsonl", "renamed")
    infos = list_sessions()

    assert len(calls) == 1, "a rename must not force a re-read"
    assert infos[0].title == "renamed"
