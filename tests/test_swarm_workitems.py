import pytest

from zenagent.app.swarm.room import SwarmStore
from zenagent.app.swarm.workitems import WorkItemStore


def _store(tmp_path):
    room = SwarmStore(tmp_path)
    items = WorkItemStore(room)
    items.load()
    return room, items


def test_open_creates_open_item(tmp_path):
    _, items = _store(tmp_path)
    item = items.open("m1", "修好上传超时", opened_by_kind="user", opened_by_id="you")
    assert item.state == "open"
    assert item.root_message_id == "m1"
    assert items.items[item.id] == item


def test_review_then_closed_transition(tmp_path):
    _, items = _store(tmp_path)
    item = items.open("m1", "t", opened_by_kind="user", opened_by_id="you")
    item = items.update(item.id, state="review")
    assert item.state == "review"
    item = items.update(item.id, state="closed", closed_at=123)
    assert item.state == "closed"


def test_closed_is_terminal(tmp_path):
    _, items = _store(tmp_path)
    item = items.open("m1", "t", opened_by_kind="user", opened_by_id="you")
    items.update(item.id, state="closed")
    with pytest.raises(ValueError):
        items.update(item.id, state="open")


def test_score_allowed_after_close_and_nullable(tmp_path):
    _, items = _store(tmp_path)
    item = items.open("m1", "t", opened_by_kind="user", opened_by_id="you")
    items.update(item.id, state="closed")
    item = items.score(item.id, 4, "还行")
    assert (item.score, item.score_note) == (4, "还行")
    with pytest.raises(ValueError):
        items.score(item.id, 9, None)


def test_load_replays_and_last_wins(tmp_path):
    _room, items = _store(tmp_path)
    item = items.open("m1", "t", opened_by_kind="user", opened_by_id="you")
    items.update(item.id, state="review")
    again = WorkItemStore(SwarmStore(tmp_path))
    again.load()
    assert again.items[item.id].state == "review"


def test_malformed_line_is_skipped_with_warning(tmp_path):
    (tmp_path / "work_items.jsonl").write_text("{not json\n", encoding="utf-8")
    room = SwarmStore(tmp_path)
    items = WorkItemStore(room)
    items.load()
    assert items.items == {}
    assert items.warnings
