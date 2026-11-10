from dataclasses import replace

import pytest

from zenagent.app.swarm.room import (
    RoomMessage,
    SwarmStore,
    TaskRecord,
    is_terminal,
    parse_mentions,
)


def _message(**overrides) -> RoomMessage:
    fields = dict(
        id="m1",
        ts=1,
        author_kind="user",
        author_id="you",
        text="hi",
        mentions=[],
        task_id=None,
        chain_depth=0,
    )
    fields.update(overrides)
    return RoomMessage(**fields)


def _task(**overrides) -> TaskRecord:
    fields = dict(
        id="t1",
        assignee="research",
        state="queued",
        source_message_id="m1",
        created_by_kind="user",
        created_by_id="you",
        chain_id="c1",
        chain_depth=0,
        chain_cost=0.0,
        created_at=1,
        updated_at=1,
    )
    fields.update(overrides)
    return TaskRecord(**fields)


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
