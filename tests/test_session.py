"""Session persistence tests: round trip, torn tail, latest-session ordering."""


import pytest

import pyagent.agent.session as session_module
from pyagent.agent.session import Session, latest_session, session_path
from pyagent.types import AssistantMessage, TextContent, Usage, UserMessage


def user(text: str, timestamp: int = 1) -> UserMessage:
    return UserMessage(content=text, timestamp=timestamp)


def assistant(text: str, timestamp: int = 2) -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text=text)],
        provider="test",
        model="m",
        usage=Usage(),
        stop_reason="stop",
        timestamp=timestamp,
    )


@pytest.fixture(autouse=True)
def isolated_sessions(tmp_path, monkeypatch):
    root = tmp_path / "sessions"
    monkeypatch.setattr(session_module, "sessions_dir", lambda: root)
    return root


async def test_append_then_load_round_trips( tmp_path ):
    path = tmp_path / "s.jsonl"
    session = Session(path)
    original = [user("hello"), assistant("hi"), user("again")]
    for message in original:
        session.append(message)

    loaded = Session(path).load()
    assert loaded == original
    assert path.read_text().count("\n") == 3


async def test_torn_tail_is_skipped_with_a_warning(tmp_path):
    path = tmp_path / "s.jsonl"
    session = Session(path)
    for message in [user("a"), assistant("b"), user("c")]:
        session.append(message)
    # simulate a process killed mid-write
    with path.open("a") as handle:
        handle.write('{"role": "assistant", "content": [{"type": "te')

    reloaded = Session(path)
    messages = reloaded.load()
    assert len(messages) == 3
    assert reloaded.warnings, "a skipped line must be reported"
    assert "skipped unreadable line" in reloaded.warnings[0]


async def test_missing_file_loads_empty(tmp_path):
    session = Session(tmp_path / "absent.jsonl")
    assert session.load() == []
    assert session.warnings == []


async def test_append_keeps_the_cached_view_in_sync(tmp_path):
    session = Session(tmp_path / "s.jsonl")
    assert session.messages == []
    session.append(user("one"))
    session.append(assistant("two"))
    assert [type(m) for m in session.messages] == [UserMessage, AssistantMessage]
    assert len(Session(session.path).load()) == 2


def test_session_path_is_timestamped_and_prefixed(isolated_sessions):
    path = session_path("compact")
    assert path.parent == isolated_sessions
    assert path.name.endswith("-compact.jsonl")
    assert len(path.stem.split("-")) >= 2


def test_latest_session_picks_the_newest_name(isolated_sessions):
    isolated_sessions.mkdir(parents=True)
    for name in ("20260101-000000-a.jsonl", "20260102-000000-b.jsonl", "20251231-000000-c.jsonl"):
        (isolated_sessions / name).write_text("")
    assert latest_session().name == "20260102-000000-b.jsonl"


def test_latest_session_none_when_empty(isolated_sessions):
    assert latest_session() is None
