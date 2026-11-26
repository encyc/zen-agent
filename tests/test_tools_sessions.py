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
