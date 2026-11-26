"""The three cross-session tools: read-only, project-pinned, never writing."""

import json
import re
from pathlib import Path

import pytest

from zenagent import project
from zenagent.tools.sessions import build_session_tools


@pytest.fixture
def sessions_home(tmp_path):
    """Two projects with one session each; returns (home, root_a, root_b).

    No HOME juggling: `isolated_project_home` (autouse, conftest.py:26) already
    pinned project.zenagent_home to tmp_path.
    """
    root_a = tmp_path / "alpha"
    root_b = tmp_path / "beta"
    for root, said in ((root_a, "we decided on the lease"), (root_b, "beta only")):
        root.mkdir()
        target = project.project_sessions_dir(root=root)
        target.mkdir(parents=True)
        (target / "20260101-000000.jsonl").write_text(
            json.dumps({"role": "user", "content": said, "timestamp": 1}) + "\n",
            encoding="utf-8",
        )
    return tmp_path, root_a, root_b


def tool_named(tools, name):
    return next(tool for tool in tools if tool.name == name)


async def call(tools, name, **args):
    return await tool_named(tools, name).execute("call_1", args, None)


async def test_list_sessions_shows_the_injected_root_only(sessions_home):
    _, root_a, _ = sessions_home
    result = await call(build_session_tools(root_a), "list_sessions")
    text = result.content[0].text
    assert "we decided on the lease" in text
    assert "beta only" not in text


async def test_list_sessions_all_reaches_other_projects(sessions_home):
    _, root_a, _ = sessions_home
    result = await call(build_session_tools(root_a), "list_sessions", all=True)
    text = result.content[0].text
    assert "we decided on the lease" in text
    assert "beta only" in text


async def test_the_tools_declare_read_and_accept_no_path_argument():
    tools = build_session_tools(Path("/tmp/x"))
    assert [tool.name for tool in tools] == [
        "list_sessions",
        "search_sessions",
        "read_session",
    ]
    for tool in tools:
        assert tool.approval == "read", tool.name
        properties = set(tool.parameters["properties"])
        assert not properties & {"path", "file", "dir", "directory"}, tool.name


async def test_each_row_is_id_count_date_and_title(sessions_home):
    _, root_a, _ = sessions_home
    result = await call(build_session_tools(root_a), "list_sessions")
    row = result.content[0].text.splitlines()[0]
    assert re.fullmatch(
        r"20260101-000000  1 msgs  \d{4}-\d{2}-\d{2}  we decided on the lease", row
    ), row


async def test_all_rows_carry_the_project_label(sessions_home):
    _, root_a, _ = sessions_home
    result = await call(build_session_tools(root_a), "list_sessions", all=True)
    labelled = [
        line for line in result.content[0].text.splitlines() if "[alpha-" in line
    ]
    assert labelled, result.content[0].text


async def test_listing_never_writes_the_index_sidecar(sessions_home):
    """Read-only means read-only: ``cache=False`` keeps ``_index.json`` away."""
    _, root_a, _ = sessions_home
    sessions_dir = project.project_sessions_dir(root=root_a)
    await call(build_session_tools(root_a), "list_sessions")
    assert not (sessions_dir / "_index.json").exists()
    assert not list(sessions_dir.glob(".tmp-index-*"))


def write_session(root, name, lines):
    target = project.project_sessions_dir(root=root)
    target.mkdir(parents=True, exist_ok=True)
    (target / f"{name}.jsonl").write_text(
        "".join(json.dumps(o, ensure_ascii=False) + "\n" for o in lines),
        encoding="utf-8",
    )
    return target


def assistant(*blocks, timestamp):
    """A complete assistant record.

    ``Session.load`` requires provider/model/usage/stop_reason and silently
    skips a line without them; a skipped line means zero messages, and
    ``list_sessions`` (the candidate source of a search) drops such a session
    as an empty leftover. The minimal shape would therefore make the
    tool-call-only session invisible to the very search that must find it.
    """
    return {
        "role": "assistant",
        "provider": "deepseek",
        "model": "deepseek-chat",
        "usage": {},
        "stop_reason": "stop",
        "content": list(blocks),
        "timestamp": timestamp,
    }


@pytest.fixture
def search_home(tmp_path):
    root = tmp_path / "alpha"
    root.mkdir()
    write_session(
        root,
        "20260101-000000",
        [
            {"role": "user", "content": "we talked about the colour blue", "timestamp": 1},
            assistant({"type": "text", "text": "yes, blue was chosen"}, timestamp=2),
        ],
    )
    write_session(
        root,
        "20260102-000000",
        [
            assistant(
                {"type": "toolCall", "id": "c1", "name": "edit_file",
                 "arguments": {"path": "theme/blue.css"}},
                timestamp=3,
            ),
        ],
    )
    return root


async def test_search_defaults_to_conversation_text(search_home):
    result = await call(build_session_tools(search_home), "search_sessions", query="blue")
    text = result.content[0].text
    assert "20260101-000000" in text
    assert "20260102-000000" not in text  # only its tool arguments mention blue
    assert result.details["sessions"] == 1


async def test_search_tools_level_finds_tool_arguments(search_home):
    result = await call(
        build_session_tools(search_home), "search_sessions", query="blue", include="tools"
    )
    assert "20260102-000000" in result.content[0].text
    assert result.details["sessions"] == 2


async def test_empty_level_names_the_level_that_would_match(search_home):
    result = await call(build_session_tools(search_home), "search_sessions", query="theme/blue")
    text = result.content[0].text
    assert "no matches" in text
    assert 'include="tools"' in text


async def test_skip_pages_by_session_not_by_hit(tmp_path):
    """One page holds MAX_SESSIONS_PER_PAGE sessions, and skip counts those.

    Built past one full page on purpose: with fewer sessions than the page
    size, next_skip would be None and the test would pass without ever
    exercising pagination.
    """
    from zenagent.tools.sessions import MAX_SESSIONS_PER_PAGE

    root = tmp_path / "many"
    root.mkdir()
    total = MAX_SESSIONS_PER_PAGE + 5
    for n in range(total):
        write_session(root, f"20260101-{n:06d}", [
            {"role": "user", "content": f"deepseek note {n}", "timestamp": n},
            {"role": "user", "content": f"deepseek again {n}", "timestamp": n},
        ])
    tools = build_session_tools(root)
    first = await call(tools, "search_sessions", query="deepseek")
    assert first.details["sessions"] == total          # every session counted
    assert first.details["next_skip"] == MAX_SESSIONS_PER_PAGE  # but one page shown
    second = await call(
        tools, "search_sessions", query="deepseek", skip=MAX_SESSIONS_PER_PAGE
    )
    # Page membership, not a specific id: sessions written in the same second
    # share an mtime, so which id lands on page 1 is not deterministic. What is
    # deterministic is that the two pages are the same size and do not overlap.
    def ids(result):
        return {
            line.split()[0]
            for line in result.content[0].text.splitlines()
            if line and not line.startswith(" ")
        }

    page_one, page_two = ids(first), ids(second)
    assert len(page_one) == MAX_SESSIONS_PER_PAGE
    assert len(page_two) == 5
    assert not page_one & page_two


async def test_max_hits_caps_excerpts_per_session(search_home):
    """max_hits caps excerpts, not the header: the hit count stays true."""
    result = await call(
        build_session_tools(search_home), "search_sessions", query="blue", max_hits=1
    )
    text = result.content[0].text
    assert text.count("#") == 1          # max_hits=1 shows one excerpt line
    assert result.details["hits"] == 2   # both matches still counted in the header


async def test_an_invalid_regex_is_an_error_not_a_crash(search_home):
    result = await call(build_session_tools(search_home), "search_sessions", query="[unclosed")
    assert result.is_error
    assert "invalid regular expression" in result.content[0].text


async def test_search_all_reaches_other_projects(sessions_home):
    _, root_a, _ = sessions_home
    tools = build_session_tools(root_a)
    scoped = await call(tools, "search_sessions", query="beta only")
    assert scoped.details["sessions"] == 0
    everywhere = await call(tools, "search_sessions", query="beta only", all=True)
    assert everywhere.details["sessions"] == 1
    assert "beta only" in everywhere.content[0].text


async def test_an_unknown_include_is_an_error(search_home):
    result = await call(
        build_session_tools(search_home),
        "search_sessions",
        query="blue",
        include="everything",
    )
    assert result.is_error
    assert "unknown include" in result.content[0].text


async def test_read_session_returns_the_numbered_messages(search_home):
    result = await call(
        build_session_tools(search_home), "read_session", id="20260101-000000"
    )
    text = result.content[0].text
    assert "#0 user" in text
    assert "#1 assistant" in text
    assert "blue was chosen" in text


async def test_read_session_offset_matches_the_search_message_number(search_home):
    """The `#N` a search reports must resolve to the same message here."""
    tools = build_session_tools(search_home)
    found = await call(tools, "search_sessions", query="blue was chosen")
    assert "#1" in found.content[0].text
    page = await call(tools, "read_session", id="20260101-000000", offset=1)
    assert "#1 assistant" in page.content[0].text
    assert "#0 user" not in page.content[0].text


async def test_read_session_pages_with_next_offset(tmp_path):
    root = tmp_path / "alpha"
    root.mkdir()
    write_session(root, "20260101-000000", [
        {"role": "user", "content": f"line {n}", "timestamp": n} for n in range(5)
    ])
    tools = build_session_tools(root)
    first = await call(tools, "read_session", id="20260101-000000", limit=2)
    assert first.details["next_offset"] == 2
    last = await call(tools, "read_session", id="20260101-000000", offset=4, limit=2)
    assert last.details["next_offset"] is None
    assert "line 4" in last.content[0].text


async def test_read_session_omits_tool_results_by_default(search_home):
    tools = build_session_tools(search_home)
    page = await call(tools, "read_session", id="20260102-000000")
    assert "theme/blue.css" not in page.content[0].text
    expanded = await call(tools, "read_session", id="20260102-000000", include="tools")
    assert "theme/blue.css" in expanded.content[0].text


async def test_an_unknown_session_id_is_an_error_naming_the_known_ones(search_home):
    result = await call(build_session_tools(search_home), "read_session", id="nope")
    assert result.is_error
    assert "20260101-000000" in result.content[0].text


async def test_an_offset_past_the_end_is_an_error_not_an_empty_page(search_home):
    result = await call(
        build_session_tools(search_home), "read_session", id="20260101-000000", offset=99
    )
    assert result.is_error
    assert "past the end" in result.content[0].text


async def test_read_session_never_returns_thinking(tmp_path):
    """A session whose only content is reasoning renders nothing readable."""
    root = tmp_path / "alpha"
    root.mkdir()
    write_session(root, "20260101-000000", [
        {"role": "user", "content": "say something", "timestamp": 1},
        assistant({"type": "thinking", "thinking": "SECRET-REASONING"}, timestamp=2),
    ])
    tools = build_session_tools(root)
    for level in ("conversation", "tools", "all"):
        page = await call(tools, "read_session", id="20260101-000000", include=level)
        assert "SECRET-REASONING" not in page.content[0].text, level


async def test_a_textless_page_still_advances_next_offset(tmp_path):
    """Paging is by message line, so an all-toolCall page must not stall."""
    root = tmp_path / "alpha"
    root.mkdir()
    write_session(root, "20260101-000000", [
        assistant({"type": "toolCall", "id": "c1", "name": "bash",
                   "arguments": {"command": "ls"}}, timestamp=1),
        assistant({"type": "toolCall", "id": "c2", "name": "bash",
                   "arguments": {"command": "pwd"}}, timestamp=2),
        {"role": "user", "content": "after the calls", "timestamp": 3},
    ])
    tools = build_session_tools(root)
    page = await call(tools, "read_session", id="20260101-000000", limit=2)
    assert page.details["next_offset"] == 2
    assert "after the calls" not in page.content[0].text
    rest = await call(tools, "read_session", id="20260101-000000", offset=2)
    assert "after the calls" in rest.content[0].text


async def test_a_traversing_id_is_refused_not_resolved(sessions_home):
    """`id` is a stem, never a path: nothing may be joined onto the store root."""
    _, root_a, _ = sessions_home
    tools = build_session_tools(root_a)
    for bogus in ("../../secrets", "/etc/passwd", "20260101-000000.jsonl"):
        result = await call(tools, "read_session", id=bogus)
        assert result.is_error, bogus
        assert "no session" in result.content[0].text.lower() or "not found" in (
            result.content[0].text.lower()
        ), bogus
