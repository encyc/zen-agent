import json
import os
from dataclasses import replace

import pytest

from zenagent.app.swarm.room import (
    RoomMessage,
    RoomRef,
    SwarmStore,
    TaskRecord,
    _message_record,
    _task_record,
    is_terminal,
    parse_mentions,
)


def _message(**overrides) -> RoomMessage:
    fields = {
        "id": "m1",
        "ts": 1,
        "author_kind": "user",
        "author_id": "you",
        "text": "hi",
        "mentions": [],
        "task_id": None,
        "chain_depth": 0,
    }
    fields.update(overrides)
    return RoomMessage(**fields)


def _task(**overrides) -> TaskRecord:
    fields = {
        "id": "t1",
        "assignee": "research",
        "state": "queued",
        "source_message_id": "m1",
        "created_by_kind": "user",
        "created_by_id": "you",
        "chain_id": "c1",
        "chain_depth": 0,
        "chain_cost": 0.0,
        "created_at": 1,
        "updated_at": 1,
    }
    fields.update(overrides)
    return TaskRecord(**fields)


def test_chain_spend_accumulates_each_run_and_survives_reload(tmp_path):
    store = SwarmStore(tmp_path)
    queued = _task(id="t1", state="queued")
    store.append_task(queued)
    store.append_task(replace(queued, state="running"))
    store.append_task(replace(queued, state="done", chain_cost=0.7))

    assert store.chain_spend["c1"] == pytest.approx(0.7)

    store2 = SwarmStore(tmp_path)
    store2.load()

    assert store2.chain_spend["c1"] == pytest.approx(0.7)


def test_chain_spend_sums_sibling_branches(tmp_path):
    store = SwarmStore(tmp_path)
    for task_id in ("t1", "t2"):
        queued = _task(id=task_id, state="queued", chain_cost=0.0)
        store.append_task(queued)
        store.append_task(replace(queued, state="running"))
        store.append_task(replace(queued, state="done", chain_cost=0.7))

    assert store.chain_spend["c1"] == pytest.approx(1.4)


def test_message_and_task_roundtrip(tmp_path):
    store = SwarmStore(tmp_path)
    store.append_message(_message())
    store.append_task(_task())

    store2 = SwarmStore(tmp_path)
    store2.load()

    assert [message.id for message in store2.messages] == ["m1"]
    assert store2.messages[0].author_kind == "user"
    assert store2.messages[0].text == "hi"
    assert store2.tasks["t1"].assignee == "research"
    assert store2.tasks["t1"].source_message_id == "m1"


def test_last_state_wins(tmp_path):
    store = SwarmStore(tmp_path)
    task = _task()
    store.append_task(task)
    store.append_task(replace(task, state="running", updated_at=2))
    store.append_task(replace(task, state="done", updated_at=3))

    store2 = SwarmStore(tmp_path)
    store2.load()

    assert store2.tasks["t1"].state == "done"
    assert store2.tasks["t1"].updated_at == 3


def test_invalid_transition_is_rejected(tmp_path):
    store = SwarmStore(tmp_path)
    task = _task()
    store.append_task(task)
    store.append_task(replace(task, state="running"))
    store.append_task(replace(task, state="done"))

    with pytest.raises(ValueError):
        store.append_task(replace(task, state="running"))


def test_reconcile_fails_every_nonterminal_task(tmp_path):
    store = SwarmStore(tmp_path)
    store.append_task(_task(id="t1", state="queued"))
    store.append_task(_task(id="t2", state="queued"))
    store.append_task(_task(id="t2", state="running"))

    messages = store.reconcile()

    assert store.tasks["t1"].state == "failed"
    assert store.tasks["t1"].reason == "interrupted"
    assert store.tasks["t2"].state == "failed"
    assert store.tasks["t2"].reason == "interrupted"
    assert len(messages) == 1
    assert messages[0].author_kind == "system"
    assert store.messages == messages
    assert is_terminal(store.tasks["t1"].state)


def test_parse_mentions_dedupes_and_keeps_order():
    assert parse_mentions("@research 和 @coder，再 @research") == ["research", "coder"]


def test_append_fsyncs(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(os, "fsync", lambda fd: calls.append(fd))
    store = SwarmStore(tmp_path)
    store.append_message(_message(id="m1"))
    assert calls, "append must fsync"


def test_load_truncates_unterminated_tail(tmp_path):
    store = SwarmStore(tmp_path)
    good = json.dumps(_message_record(_message(id="m1")), ensure_ascii=False)
    store.room_path.write_text(
        good + "\n" + '{"type": "message", "id": "torn"', encoding="utf-8"
    )

    store.load()

    assert store.room_path.read_text().endswith("\n")
    assert "torn" not in store.room_path.read_text()
    store.append_message(_message(id="m2"))
    assert len(store.room_path.read_text().splitlines()) == 2


def test_load_truncates_parseable_unterminated_tail(tmp_path):
    # A crash after the JSON body but before its newline leaves a tail that
    # parses yet is uncommitted; load must still drop it, or the next append
    # concatenates onto it and merges two records into one malformed line.
    store = SwarmStore(tmp_path)
    good = json.dumps(_message_record(_message(id="m1")), ensure_ascii=False)
    torn = json.dumps(_message_record(_message(id="m2")), ensure_ascii=False)
    store.room_path.write_text(good + "\n" + torn, encoding="utf-8")

    store.load()

    assert store.room_path.read_text().endswith("\n")
    assert [message.id for message in store.messages] == ["m1"]
    store.append_message(_message(id="m3"))
    lines = store.room_path.read_text().splitlines()
    assert [json.loads(line)["id"] for line in lines] == ["m1", "m3"]


def test_message_record_carries_a_ref_snapshot_and_images(tmp_path):
    store = SwarmStore(tmp_path)
    ref = RoomRef(id="m0", author_kind="agent", author_id="research", text="结论是 Z")
    message = _message(ref=ref, images=["/tmp/a.png"])
    store.append_message(message)

    record = _message_record(message)
    assert record["ref"] == {
        "id": "m0",
        "author": {"kind": "agent", "id": "research"},
        "text": "结论是 Z",
    }
    assert record["images"] == ["/tmp/a.png"]


def test_replayed_message_restores_ref_and_images(tmp_path):
    store = SwarmStore(tmp_path)
    store.append_message(
        _message(
            id="m1",
            ref=RoomRef(id="m0", author_kind="agent", author_id="research", text="Z"),
            images=["/tmp/a.png", "/tmp/b.png"],
        )
    )
    reloaded = SwarmStore(tmp_path)
    reloaded.load()

    message = reloaded.messages[0]
    assert message.ref is not None
    assert (message.ref.id, message.ref.author_id, message.ref.text) == ("m0", "research", "Z")
    assert message.images == ["/tmp/a.png", "/tmp/b.png"]


def test_a_pre_change_record_still_loads(tmp_path):
    (tmp_path / "room.jsonl").write_text(
        json.dumps({"id": "m_old", "ts": 1, "author": {"kind": "user", "id": "you"}, "text": "hi"})
        + "\n",
        encoding="utf-8",
    )
    store = SwarmStore(tmp_path)
    store.load()

    assert store.messages[0].ref is None
    assert store.messages[0].images == []
    assert store.warnings == []


def test_a_malformed_ref_is_ignored_without_dropping_the_message(tmp_path):
    (tmp_path / "room.jsonl").write_text(
        json.dumps(
            {
                "id": "m1",
                "ts": 1,
                "author": {"kind": "user", "id": "you"},
                "text": "hi",
                "ref": "not-an-object",
                "images": "nope",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    store = SwarmStore(tmp_path)
    store.load()

    assert store.messages[0].text == "hi"
    assert store.messages[0].ref is None
    assert store.messages[0].images == []


def test_task_roundtrip_keeps_work_item_and_run_cost():
    task = _task(work_item_id="w1", run_cost=0.25)

    raw = _task_record(task)

    assert raw["workItemId"] == "w1"
    assert raw["runCost"] == 0.25


def test_legacy_task_line_has_no_work_item(tmp_path):
    (tmp_path / "tasks.jsonl").write_text(
        json.dumps({"type": "task", "id": "t1", "assignee": "a", "state": "queued"}) + "\n",
        encoding="utf-8",
    )
    store = SwarmStore(tmp_path)
    store.load()

    assert store.tasks["t1"].work_item_id is None
    assert store.tasks["t1"].run_cost == 0.0
