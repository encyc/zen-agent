"""Tests for the project memory store (M1: entry read/write/journal)."""

from zenagent.agent.memory_store import (
    MAX_INDEX_ENTRIES,
    MAX_TITLE_CHARS,
    MEMORY_DIR,
    MemoryEntry,
    MemoryStore,
)


def test_roundtrip_and_list(tmp_path):
    store = MemoryStore(tmp_path)
    entry = store.write_entry(
        title="依赖选型", body="用 httpx 而非 requests，因为连接池", source="test"
    )
    assert store.read_entry(entry.id) == entry
    assert store.list_entries() == [entry]
    assert (tmp_path / MEMORY_DIR / "entries").is_dir()  # 懒创建


def test_malformed_entry_degrades(tmp_path):
    path = tmp_path / MEMORY_DIR / "entries" / "beef.md"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\xff\xfe binary junk")
    store = MemoryStore(tmp_path)
    entry = store.read_entry("beef")
    assert entry is not None and entry.title  # 降级不崩，title 非 None


def test_update_rewrites_and_bumps_updated(tmp_path):
    store = MemoryStore(tmp_path)
    entry = store.write_entry(title="t", body="b", source="s")
    updated = store.update_entry(entry.id, title="t2", body="b2", reason="fix")
    assert updated is not None
    assert updated.title == "t2" and updated.body == "b2"
    assert updated.created == entry.created  # created 不变
    assert store.read_entry(entry.id) == updated


def test_constants():
    assert MEMORY_DIR == ".zenagent/memory"
    assert MAX_INDEX_ENTRIES == 64
    assert MAX_TITLE_CHARS == 80
    assert MemoryEntry.__dataclass_fields__.keys() == {
        "id",
        "title",
        "body",
        "created",
        "updated",
        "source",
    }
