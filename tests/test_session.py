"""Session persistence tests: round trip, torn tail, latest-session ordering."""


from pathlib import Path

import pytest

import zenagent.agent.session as session_module
from zenagent import project
from zenagent.agent.session import (
    Session,
    all_session_dirs,
    brief,
    latest_session,
    list_sessions,
    read_session_titles,
    session_path,
    session_title,
    set_session_title,
)
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


@pytest.fixture
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


# ---------------------------------------------------------------------------
# Session naming: derived brief + titles sidecar
# ---------------------------------------------------------------------------


def test_brief_takes_the_first_meaningful_line():
    assert brief("first line\nsecond line") == "first line"
    assert brief("\n\n  ##   Fix   the bug\nthen more") == "Fix the bug"


def test_brief_strips_markdown_prefixes():
    assert brief("# Heading") == "Heading"
    assert brief("> quoted question") == "quoted question"
    assert brief("- bullet item") == "bullet item"
    assert brief("* starred item") == "starred item"
    assert brief("```python") == "python"
    assert brief("  >  > nested quote") == "nested quote"


def test_brief_collapses_whitespace():
    assert brief("  hello\t\tworld   ") == "hello world"


def test_brief_truncates_long_text_with_an_ellipsis():
    shortened = brief("abcdefghijklmnopqrstuvwxyz", limit=10)
    assert shortened == "abcdefghi…"
    assert len(shortened) == 10


def test_brief_uses_a_placeholder_for_empty_text():
    assert brief("") == "(empty)"
    assert brief("   \n\t ") == "(empty)"
    assert brief("\n#\n>") == "(empty)"


def test_read_session_titles_tolerates_missing_and_broken_files(isolated_sessions):
    assert read_session_titles() == {}  # sessions dir does not exist yet
    isolated_sessions.mkdir(parents=True)
    assert read_session_titles() == {}  # sidecar does not exist yet

    sidecar = isolated_sessions / ".titles.json"
    sidecar.write_text("{oops", encoding="utf-8")
    assert read_session_titles() == {}
    sidecar.write_text('["not", "a", "dict"]', encoding="utf-8")
    assert read_session_titles() == {}


def test_read_session_titles_ignores_non_string_entries(isolated_sessions):
    isolated_sessions.mkdir(parents=True)
    (isolated_sessions / ".titles.json").write_text(
        '{"a.jsonl": "ok", "b.jsonl": 42, "c.jsonl": ""}', encoding="utf-8"
    )

    assert read_session_titles() == {"a.jsonl": "ok"}


def test_session_title_derives_a_brief_from_the_first_user_message(isolated_sessions):
    isolated_sessions.mkdir(parents=True)
    path = _write_session(
        isolated_sessions, "chat.jsonl", ["first question", "second question"]
    )

    assert session_title(path) == "first question"


def test_stored_title_overrides_the_derived_brief(isolated_sessions):
    isolated_sessions.mkdir(parents=True)
    path = _write_session(isolated_sessions, "chat.jsonl", ["hello there"])

    stored = set_session_title(path, "  Renamed   chat ")

    assert stored == "Renamed chat"
    assert read_session_titles() == {"chat.jsonl": "Renamed chat"}
    assert session_title(path) == "Renamed chat"
    assert Session(path).read_info().title == "Renamed chat"

    from zenagent.agent.session import list_sessions

    assert list_sessions()[0].title == "Renamed chat"


def test_set_session_title_caps_long_titles(isolated_sessions):
    isolated_sessions.mkdir(parents=True)
    path = _write_session(isolated_sessions, "chat.jsonl", ["hello"])

    stored = set_session_title(path, "x" * 200)

    assert stored == "x" * 80
    assert len(stored) == 80
    assert set_session_title(path, "line one\nline two") == "line one line two"


def test_set_session_title_rejects_blank_titles(isolated_sessions):
    isolated_sessions.mkdir(parents=True)
    path = _write_session(isolated_sessions, "chat.jsonl", ["hello"])

    for blank in ("", "   ", "\n\t "):
        with pytest.raises(ValueError):
            set_session_title(path, blank)
    assert read_session_titles() == {}


def test_corrupt_sidecar_falls_back_to_the_derived_title(isolated_sessions):
    isolated_sessions.mkdir(parents=True)
    path = _write_session(isolated_sessions, "chat.jsonl", ["derived name"])
    (isolated_sessions / ".titles.json").write_text(
        "{ this is not json", encoding="utf-8"
    )

    assert session_title(path) == "derived name"
    assert Session(path).read_info().title == "derived name"


def test_empty_session_gets_a_readable_placeholder_title(isolated_sessions):
    isolated_sessions.mkdir(parents=True)
    path = isolated_sessions / "empty.jsonl"
    path.write_text("", encoding="utf-8")

    assert session_title(path) == "(empty)"
    assert Session(path).read_info().title == "(empty)"


def test_titles_live_in_a_sidecar_not_in_the_jsonl(isolated_sessions):
    isolated_sessions.mkdir(parents=True)
    path = _write_session(isolated_sessions, "chat.jsonl", ["hello"])
    before = path.read_bytes()

    set_session_title(path, "Renamed")

    assert path.read_bytes() == before  # append-only JSONL is untouched
    assert [type(m) for m in Session(path).load()] == [UserMessage, AssistantMessage]
    assert (isolated_sessions / ".titles.json").is_file()


def test_fork_at_does_not_inherit_the_source_title(isolated_sessions):
    isolated_sessions.mkdir(parents=True)
    source = _write_session(isolated_sessions, "chat.jsonl", ["hello"])
    set_session_title(source, "Source name")

    forked = Session(source).fork_at(1)

    assert forked.name != source.name
    assert session_title(forked) == "hello"


def test_rewrite_does_not_inherit_the_source_title(isolated_sessions):
    isolated_sessions.mkdir(parents=True)
    source = _write_session(isolated_sessions, "chat.jsonl", ["hello"])
    set_session_title(source, "Source name")

    rewritten = Session(source).rewrite([user("fresh start")])

    assert session_title(rewritten) == "fresh start"


def test_unique_session_path_never_reuses_an_existing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(session_module, "sessions_dir", lambda: tmp_path)
    first = session_module.unique_session_path()
    first.touch()
    second = session_module.unique_session_path()
    assert second != first
    assert not second.exists()


# ---------------------------------------------------------------------------
# Project scoping: default view, explicit all view, per-directory sidecars
# ---------------------------------------------------------------------------


def _enter_project(base: Path, monkeypatch, name: str) -> Path:
    """Turn ``base/name`` into a git repo and make it the current project."""
    root = base / name
    (root / ".git").mkdir(parents=True)
    monkeypatch.chdir(root)
    project.write_project_meta()
    return root


def test_list_sessions_defaults_to_the_current_project(tmp_path, monkeypatch):
    legacy = project.legacy_sessions_dir()
    legacy.mkdir(parents=True)
    _write_session(legacy, "legacy.jsonl", ["legacy chat"], mtime=500)

    alpha = _enter_project(tmp_path, monkeypatch, "alpha")
    _write_session(project.project_sessions_dir(), "alpha.jsonl", ["alpha chat"], mtime=1_000)
    beta = _enter_project(tmp_path, monkeypatch, "beta")
    _write_session(project.project_sessions_dir(), "beta.jsonl", ["beta chat"], mtime=2_000)

    monkeypatch.chdir(alpha)
    infos = list_sessions()
    assert [info.path.name for info in infos] == ["alpha.jsonl"]
    assert infos[0].project == "alpha"

    monkeypatch.chdir(beta)
    infos = list_sessions()
    assert [info.path.name for info in infos] == ["beta.jsonl"]
    assert infos[0].project == "beta"


def test_all_scope_merges_projects_and_legacy_with_labels(tmp_path, monkeypatch):
    legacy = project.legacy_sessions_dir()
    legacy.mkdir(parents=True)
    _write_session(legacy, "legacy.jsonl", ["legacy chat"], mtime=900)

    alpha = _enter_project(tmp_path, monkeypatch, "alpha")
    _write_session(project.project_sessions_dir(), "alpha.jsonl", ["alpha chat"], mtime=1_000)
    _enter_project(tmp_path, monkeypatch, "beta")
    _write_session(project.project_sessions_dir(), "beta.jsonl", ["beta chat"], mtime=3_000)

    monkeypatch.chdir(alpha)
    infos = list_sessions(scope="all")

    assert [info.path.name for info in infos] == [
        "beta.jsonl",
        "alpha.jsonl",
        "legacy.jsonl",
    ]
    labels = {info.path.name: info.project for info in infos}
    assert labels["alpha.jsonl"] == "alpha"
    assert labels["beta.jsonl"] == "beta"
    assert labels["legacy.jsonl"] == ""


def test_all_session_dirs_lists_legacy_and_known_projects(tmp_path, monkeypatch):
    _enter_project(tmp_path, monkeypatch, "alpha")
    alpha_dir = project.project_sessions_dir()
    alpha_dir.mkdir(parents=True)
    _enter_project(tmp_path, monkeypatch, "beta")
    beta_dir = project.project_sessions_dir()
    beta_dir.mkdir(parents=True)

    directories = all_session_dirs()

    assert directories == [project.legacy_sessions_dir(), *sorted([alpha_dir, beta_dir])]


def test_same_named_sessions_in_two_projects_do_not_collide(tmp_path, monkeypatch):
    alpha = _enter_project(tmp_path, monkeypatch, "alpha")
    alpha_dir = project.project_sessions_dir()
    _write_session(alpha_dir, "chat.jsonl", ["alpha topic"], mtime=1_000)

    beta = _enter_project(tmp_path, monkeypatch, "beta")
    beta_dir = project.project_sessions_dir()
    _write_session(beta_dir, "chat.jsonl", ["beta topic"], mtime=2_000)

    assert alpha_dir != beta_dir
    monkeypatch.chdir(alpha)
    assert [info.title for info in list_sessions()] == ["alpha topic"]
    monkeypatch.chdir(beta)
    assert [info.title for info in list_sessions()] == ["beta topic"]


def test_session_titles_are_scoped_to_the_session_directory(tmp_path, monkeypatch):
    _enter_project(tmp_path, monkeypatch, "alpha")
    alpha_dir = project.project_sessions_dir()
    alpha_session = _write_session(alpha_dir, "chat.jsonl", ["alpha chat"])

    _enter_project(tmp_path, monkeypatch, "beta")
    beta_dir = project.project_sessions_dir()
    beta_session = _write_session(beta_dir, "chat.jsonl", ["beta chat"])

    set_session_title(alpha_session, "Alpha name")
    set_session_title(beta_session, "Beta name")

    # The default sidecar is the current project's (beta); alpha's stays its own.
    assert read_session_titles() == {"chat.jsonl": "Beta name"}
    assert read_session_titles(alpha_dir) == {"chat.jsonl": "Alpha name"}
    assert session_title(alpha_session) == "Alpha name"
    assert session_title(beta_session) == "Beta name"
    assert Session(beta_session).read_info().title == "Beta name"


def test_latest_session_respects_scope(tmp_path, monkeypatch):
    alpha = _enter_project(tmp_path, monkeypatch, "alpha")
    _write_session(
        project.project_sessions_dir(),
        "20260101-000000-alpha.jsonl",
        ["alpha"],
        mtime=1_000,
    )

    legacy = project.legacy_sessions_dir()
    legacy.mkdir(parents=True)
    _write_session(legacy, "19990101-000000-old.jsonl", ["legacy"], mtime=9_000)

    beta = _enter_project(tmp_path, monkeypatch, "beta")
    _write_session(
        project.project_sessions_dir(),
        "20260102-000000-beta.jsonl",
        ["beta"],
        mtime=5_000,
    )

    monkeypatch.chdir(alpha)
    assert latest_session().name == "20260101-000000-alpha.jsonl"
    assert latest_session(scope="all").name == "19990101-000000-old.jsonl"

    monkeypatch.chdir(beta)
    assert latest_session().name == "20260102-000000-beta.jsonl"


def test_legacy_titles_stay_readable(tmp_path, monkeypatch):
    legacy = project.legacy_sessions_dir()
    legacy.mkdir(parents=True)
    legacy_session = _write_session(legacy, "old.jsonl", ["old legacy chat"])
    set_session_title(legacy_session, "Old saved name")

    _enter_project(tmp_path, monkeypatch, "alpha")
    _write_session(project.project_sessions_dir(), "new.jsonl", ["new chat"])

    assert session_title(legacy_session) == "Old saved name"

    infos = {info.path.name: info for info in list_sessions(scope="all")}
    assert infos["old.jsonl"].title == "Old saved name"
    assert infos["old.jsonl"].project == ""
    assert infos["new.jsonl"].title == "new chat"
    assert [info.path.name for info in list_sessions()] == ["new.jsonl"]


def test_corrupt_sidecar_is_tolerated_in_each_directory(tmp_path, monkeypatch):
    alpha = _enter_project(tmp_path, monkeypatch, "alpha")
    alpha_dir = project.project_sessions_dir()
    alpha_session = _write_session(alpha_dir, "chat.jsonl", ["alpha derived"])
    set_session_title(alpha_session, "Alpha stored")

    beta = _enter_project(tmp_path, monkeypatch, "beta")
    beta_dir = project.project_sessions_dir()
    _write_session(beta_dir, "chat.jsonl", ["beta derived"])
    (beta_dir / ".titles.json").write_text("{ this is not json", encoding="utf-8")

    monkeypatch.chdir(beta)
    assert list_sessions()[0].title == "beta derived"
    assert session_title(beta_dir / "chat.jsonl") == "beta derived"

    monkeypatch.chdir(alpha)
    assert list_sessions()[0].title == "Alpha stored"


def test_listing_rejects_an_unknown_scope(tmp_path, monkeypatch):
    _enter_project(tmp_path, monkeypatch, "alpha")

    with pytest.raises(ValueError):
        list_sessions(scope="everywhere")
    with pytest.raises(ValueError):
        latest_session(scope="everywhere")


def test_pins_sidecar_roundtrip_and_corruption(tmp_path):
    from zenagent.agent.session import read_session_pins, set_session_pin

    session = tmp_path / "s.jsonl"
    session.touch()
    assert read_session_pins(tmp_path) == set()

    assert set_session_pin(session, True) is True
    assert read_session_pins(tmp_path) == {"s.jsonl"}
    assert set_session_pin(session, False) is False
    assert read_session_pins(tmp_path) == set()

    (tmp_path / ".pins.json").write_text("{broken", encoding="utf-8")
    assert read_session_pins(tmp_path) == set()


def test_set_session_pin_rejects_missing_file(tmp_path):
    from zenagent.agent.session import set_session_pin

    with pytest.raises(ValueError):
        set_session_pin(tmp_path / "nope.jsonl", True)
