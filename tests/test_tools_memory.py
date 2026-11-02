"""Tests for the memory / memory_write tools (store-backed, tmp project root)."""

import pytest

from zenagent.agent.memory_store import MemoryStore
from zenagent.tools.memory import build_memory_tool, build_memory_write_tool


@pytest.fixture
def store(tmp_path):
    return MemoryStore(tmp_path)


@pytest.fixture
def read_tool(store):
    return build_memory_tool(store)


@pytest.fixture
def write_tool(store):
    return build_memory_write_tool(store)


async def call(tool, **args):
    return await tool.execute("c1", args, None)


def test_tiers(read_tool, write_tool):
    assert read_tool.approval == "read"
    assert write_tool.approval == "write"


async def test_add_then_read(write_tool, read_tool, store):
    result = await call(write_tool, action="add", title="依赖选型", body="用 httpx")
    assert result.is_error is False
    new_id = result.details["id"]
    assert store.read_entry(new_id) is not None

    read = await call(read_tool, action="read", id=new_id)
    assert read.is_error is False
    assert "用 httpx" in read.content[0].text
    assert "依赖选型" in read.content[0].text


async def test_update_and_journal(write_tool, read_tool, store):
    added = await call(write_tool, action="add", title="t", body="b")
    entry_id = added.details["id"]
    result = await call(
        write_tool, action="update", id=entry_id, title="t2", reason="更正"
    )
    assert result.is_error is False
    assert store.read_entry(entry_id).title == "t2"
    events = store.read_journal()
    assert events[-1]["op"] == "update" and events[-1]["reason"] == "更正"


async def test_delete_then_delete_again_is_error(write_tool, store):
    added = await call(write_tool, action="add", title="t", body="b")
    entry_id = added.details["id"]
    first = await call(write_tool, action="delete", id=entry_id)
    assert first.is_error is False
    again = await call(write_tool, action="delete", id=entry_id)
    assert again.is_error is True
    assert "not found" in again.content[0].text


async def test_search_returns_id_and_excerpt(read_tool, write_tool):
    await call(write_tool, action="add", title="依赖选型", body="用 httpx 而非 requests")
    result = await call(read_tool, action="search", query="httpx")
    assert result.is_error is False
    assert "依赖选型" in result.content[0].text


async def test_path_escape_is_rejected(read_tool, write_tool):
    for bad_id in ("../etc", "zz", "TOOLONG!", "a.b"):
        result = await call(read_tool, action="read", id=bad_id)
        assert result.is_error is True, bad_id


async def test_unknown_action_is_error(read_tool, write_tool):
    result = await call(read_tool, action="hover", id="beef")
    assert result.is_error is True
    result = await call(write_tool, action="read", id="beef")
    assert result.is_error is True  # 写工具不收读动作


async def test_empty_library_is_not_an_error(read_tool, store):
    read = await call(read_tool, action="read", id="beef")
    search = await call(read_tool, action="search", query="anything")
    assert read.is_error is False and search.is_error is False
