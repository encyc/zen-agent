"""Subagent transcript store: one JSONL file per ``task`` call, per session.

The file is header/body/footer: a ``meta`` line, the child's ``Message.to_json``
lines, and an ``end`` line. A file without ``end`` is an interrupted run and
still readable; anything unreadable is skipped like a torn session tail.
"""

import json
import time

from zenagent.agent.subagents import (
    MAX_TASK_FILE_BYTES,
    MAX_TASK_FILES,
    SubagentStore,
    carry_task_dir,
    task_dir,
    trash_task_dir,
)
from zenagent.types import AssistantMessage, TextContent, Usage, UserMessage


def session_file(tmp_path, name="20260101-120000.jsonl"):
    return tmp_path / "sessions" / name


def messages():
    return [
        UserMessage(content="research this", timestamp=1),
        AssistantMessage(
            content=[TextContent(text="done")],
            provider="p",
            model="m",
            usage=Usage(),
            stop_reason="stop",
            timestamp=2,
        ),
    ]


async def _noop():  # tests are sync; keep pytest-asyncio from complaining
    return None


def test_round_trip_writes_meta_messages_and_end(tmp_path):
    path = session_file(tmp_path)
    store = SubagentStore(path)

    store.begin("c1", "research", "deepseek-flash")
    store.append("c1", messages())
    store.finish("c1", status="ok", duration_ms=18400, cost=0.0031, tokens=812)

    [meta] = store.list()
    assert (meta.call_id, meta.name, meta.model) == (
        "c1",
        "research",
        "deepseek-flash",
    )
    assert (meta.status, meta.duration_ms, meta.cost) == ("ok", 18400, 0.0031)
    loaded = store.load("c1")
    assert loaded is not None
    assert [message.to_json() for message in loaded] == [
        message.to_json() for message in messages()
    ]

    lines = (task_dir(path) / "c1.jsonl").read_text().splitlines()
    assert json.loads(lines[0])["type"] == "meta"
    assert json.loads(lines[-1])["type"] == "end"


def test_append_only_writes_new_messages(tmp_path):
    path = session_file(tmp_path)
    store = SubagentStore(path)
    first = messages()

    store.begin("c1", "research", "m")
    store.append("c1", first)
    store.append("c1", first)  # same snapshot twice → no duplicate lines
    store.append("c1", first + [UserMessage(content="more", timestamp=3)])

    loaded = store.load("c1")
    assert loaded is not None
    assert [message.to_json()["role"] for message in loaded] == [
        "user",
        "assistant",
        "user",
    ]


def test_missing_end_is_reported_as_interrupted(tmp_path):
    path = session_file(tmp_path)
    store = SubagentStore(path)
    store.begin("c1", "research", "m")
    store.append("c1", messages())

    [meta] = store.list()

    assert meta.status == "interrupted"
    assert store.load("c1") is not None


def test_corrupt_lines_are_skipped_and_flagged(tmp_path):
    path = session_file(tmp_path)
    store = SubagentStore(path)
    store.begin("c1", "research", "m")
    store.append("c1", messages())
    store.finish("c1", status="ok", duration_ms=1, cost=0.0, tokens=0)

    target = task_dir(path) / "c1.jsonl"
    target.write_text(target.read_text() + "{not json\n")

    [meta] = store.list()
    assert meta.truncated is True
    assert store.load("c1") is not None


def test_per_file_cap_stops_appending_and_marks_truncated(tmp_path):
    path = session_file(tmp_path)
    store = SubagentStore(path)
    store.begin("c1", "research", "m")

    big = [
        AssistantMessage(
            content=[TextContent(text="x" * (MAX_TASK_FILE_BYTES // 2))],
            provider="p",
            model="m",
            usage=Usage(),
            stop_reason="stop",
            timestamp=index,
        )
        for index in range(6)
    ]
    store.append("c1", big)

    [meta] = store.list()
    assert meta.truncated is True
    lines = (task_dir(path) / "c1.jsonl").read_text().splitlines()
    assert any(json.loads(line).get("type") == "truncated" for line in lines)


def test_prune_keeps_the_newest_files(tmp_path):
    path = session_file(tmp_path)
    store = SubagentStore(path)

    for index in range(MAX_TASK_FILES + 3):
        store.begin(f"c{index}", f"task{index}", "m")
        store.append(f"c{index}", messages())
        store.finish(f"c{index}", status="ok", duration_ms=1, cost=0.0, tokens=0)
        time.sleep(0.005)

    names = {meta.call_id for meta in store.list()}

    assert len(names) == MAX_TASK_FILES
    assert "c0" not in names
    assert f"c{MAX_TASK_FILES + 2}" in names


def test_carry_copies_the_directory_to_the_new_session(tmp_path):
    old = session_file(tmp_path, "old.jsonl")
    new = session_file(tmp_path, "new.jsonl")
    store = SubagentStore(old)
    store.begin("c1", "research", "m")
    store.append("c1", messages())
    store.finish("c1", status="ok", duration_ms=1, cost=0.0, tokens=0)

    carry_task_dir(old, new)

    assert (task_dir(new) / "c1.jsonl").is_file()
    assert store.load("c1") is not None


def test_trash_moves_the_directory_as_a_unit(tmp_path):
    path = session_file(tmp_path)
    store = SubagentStore(path)
    store.begin("c1", "research", "m")
    store.finish("c1", status="ok", duration_ms=1, cost=0.0, tokens=0)
    trash = path.parent / ".trash"

    moved = trash_task_dir(path, trash)

    assert moved is not None and moved.exists()
    assert not task_dir(path).exists()


def test_no_session_path_is_memory_only(tmp_path):
    store = SubagentStore(None)
    store.begin("c1", "research", "m")
    store.append("c1", messages())
    store.finish("c1", status="ok", duration_ms=1, cost=0.0, tokens=0)

    assert store.load("c1") is None
    [meta] = store.list()
    assert meta.status == "ok"
    assert not list(tmp_path.rglob("*.jsonl")), "memory-only must not write"


def test_final_status_survives_a_body_stopped_by_the_size_cap(tmp_path):
    path = session_file(tmp_path)
    store = SubagentStore(path)
    store.begin("c1", "research", "m")
    big = [
        AssistantMessage(
            content=[TextContent(text="x" * (MAX_TASK_FILE_BYTES // 2))],
            provider="p",
            model="m",
            usage=Usage(),
            stop_reason="stop",
            timestamp=index,
        )
        for index in range(6)
    ]
    store.append("c1", big)  # exceeds the cap: body is stopped, marked truncated

    store.finish("c1", status="ok", duration_ms=5, cost=0.1, tokens=7)

    [meta] = store.list()
    assert meta.truncated is True
    assert (meta.status, meta.duration_ms, meta.cost, meta.tokens) == (
        "ok",
        5,
        0.1,
        7,
    )
