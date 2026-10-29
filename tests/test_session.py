"""Session persistence tests: round trip, torn tail, latest-session ordering."""


import pytest

import zenagent.agent.session as session_module
from zenagent.agent.session import Session, latest_session, session_path
from zenagent.types import AssistantMessage, TextContent, Usage, UserMessage


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


# ---------------------------------------------------------------------------
# S1-T8: listing, fork, rewrite
# ---------------------------------------------------------------------------


def _write_session(directory, name: str, texts: list[str], mtime: float | None = None):
    path = directory / name
    session = Session(path)
    for index, text in enumerate(texts):
        session.append(user(text, timestamp=index))
        session.append(assistant(f"re: {text}", timestamp=index))
    if mtime is not None:
        import os

        os.utime(path, (mtime, mtime))
    return path


def test_list_sessions_is_newest_first_and_skips_broken(isolated_sessions):
    isolated_sessions.mkdir(parents=True)
    _write_session(isolated_sessions, "old.jsonl", ["one"], mtime=1_000)
    _write_session(isolated_sessions, "new.jsonl", ["two", "three"], mtime=2_000)
    (isolated_sessions / "broken.jsonl").write_text("{not json\n")

    from zenagent.agent.session import list_sessions

    infos = list_sessions()
    assert [info.path.name for info in infos] == ["new.jsonl", "old.jsonl"]
    assert infos[0].messages == 4
    assert infos[0].title == "two"
    assert infos[1].title == "one"


def test_list_sessions_honours_the_limit(isolated_sessions):
    isolated_sessions.mkdir(parents=True)
    for index in range(5):
        _write_session(isolated_sessions, f"s{index}.jsonl", [f"t{index}"], mtime=index + 1)

    from zenagent.agent.session import list_sessions

    assert len(list_sessions(limit=2)) == 2


def test_fork_at_copies_the_prefix_without_touching_the_source(isolated_sessions):
    isolated_sessions.mkdir(parents=True)
    original = isolated_sessions / "orig.jsonl"
    session = Session(original)
    messages = [user("first"), assistant("a1"), user("second"), assistant("a2")]
    for message in messages:
        session.append(message)
    before = original.read_bytes()

    forked_path = session.fork_at(2)  # include the 2nd user message

    assert original.read_bytes() == before  # immutable source
    forked = Session(forked_path).load()
    assert [type(m).__name__ for m in forked] == ["UserMessage", "AssistantMessage", "UserMessage"]
    assert forked[2].content == "second"
    assert forked_path.name.endswith("-fork2.jsonl")


def test_fork_at_rejects_an_out_of_range_index(isolated_sessions):
    isolated_sessions.mkdir(parents=True)
    session = Session(isolated_sessions / "s.jsonl")
    session.append(user("only"))
    for bad in (0, -1, 2):
        try:
            session.fork_at(bad)
        except ValueError as exc:
            assert "out of range" in str(exc)
        else:  # pragma: no cover
            raise AssertionError(f"expected ValueError for {bad}")


def test_rewrite_writes_a_new_file(isolated_sessions):
    isolated_sessions.mkdir(parents=True)
    session = Session(isolated_sessions / "s.jsonl")
    for message in [user("keep"), assistant("kept")]:
        session.append(message)

    new_path = session.rewrite([user("only")])
    assert new_path != session.path
    assert Session(new_path).load()[0].content == "only"
    assert len(session.load()) == 2  # source untouched


# ---------------------------------------------------------------------------
# S1-T9: undo
# ---------------------------------------------------------------------------


def test_truncate_last_turn_drops_the_final_exchange():
    from zenagent.agent.session import truncate_last_turn

    messages = [user("q1"), assistant("a1"), user("q2"), assistant("a2")]
    truncated = truncate_last_turn(messages)
    assert [type(m).__name__ for m in truncated] == ["UserMessage", "AssistantMessage"]
    assert truncated[0].content == "q1"


def test_truncate_last_turn_drops_tool_results_with_their_turn():
    from zenagent.agent.session import truncate_last_turn
    from zenagent.types import (
        AssistantMessage,
        TextContent,
        ToolCall,
        ToolResultMessage,
        Usage,
    )

    messages = [
        user("q1"),
        assistant("a1"),
        user("run it"),
        AssistantMessage(
            content=[ToolCall(id="c1", name="bash", arguments={})],
            provider="p", model="m", usage=Usage(), stop_reason="tool_use", timestamp=3,
        ),
        ToolResultMessage(
            tool_call_id="c1",
            tool_name="bash",
            content=[TextContent(text="out")],
            is_error=False,
            timestamp=4,
        ),
    ]
    truncated = truncate_last_turn(messages)
    assert [m.content for m in truncated if isinstance(m, UserMessage)] == ["q1"]
    assert len(truncated) == 2


def test_truncate_last_turn_with_nothing_to_drop():
    from zenagent.agent.session import truncate_last_turn

    assert truncate_last_turn([]) == []
    only_assistant = [assistant("hi")]
    assert truncate_last_turn(only_assistant) == only_assistant
