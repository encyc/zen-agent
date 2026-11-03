"""Tests for the project memory store (M1: entry read/write/journal)."""

import json

from zenagent.agent.memory_store import (
    MAX_INDEX_ENTRIES,
    MAX_TITLE_CHARS,
    MEMORY_DIR,
    MemoryEntry,
    MemoryStore,
    split_title_body,
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


def test_journal_events_are_json_lines(tmp_path):
    store = MemoryStore(tmp_path)
    entry = store.write_entry(title="t", body="b", source="s")
    store.update_entry(entry.id, title="t2", reason="fix title", session="sess-1")
    store.delete_entry(entry.id)
    lines = (tmp_path / MEMORY_DIR / "journal.jsonl").read_text("utf-8").splitlines()
    events = [json.loads(line) for line in lines]
    assert [e["op"] for e in events] == ["add", "update", "delete"]
    assert events[0]["id"] == entry.id and events[0]["title"] == "t"
    assert events[1]["reason"] == "fix title" and events[1]["session"] == "sess-1"
    for event in events:
        assert set(event) == {"ts", "op", "id", "title", "reason", "session"}


def test_truncated_journal_tail_is_tolerated(tmp_path):
    store = MemoryStore(tmp_path)
    store.write_entry(title="t", body="b", source="s")
    journal = tmp_path / MEMORY_DIR / "journal.jsonl"
    journal.write_text(journal.read_text("utf-8") + '{"ts":"2026", "op":"add", ', "utf-8")
    assert store.read_journal()  # 不抛异常，尾行残缺被跳过


def test_search_matches_title_and_body_case_insensitive(tmp_path):
    store = MemoryStore(tmp_path)
    hit = store.write_entry(title="依赖选型", body="用 HTTPX 而非 requests", source="s")
    store.write_entry(title="无关", body="部署脚本", source="s")
    matches = store.search("httpx")
    assert len(matches) == 1
    entry, excerpt = matches[0]
    assert entry.id == hit.id and "HTTPX" in excerpt
    assert store.search("不存在的词") == []


def test_delete_entry(tmp_path):
    store = MemoryStore(tmp_path)
    entry = store.write_entry(title="t", body="b", source="s")
    assert store.delete_entry(entry.id) is True
    assert store.read_entry(entry.id) is None
    assert store.delete_entry(entry.id) is False  # 幂等
    events = store.read_journal()
    assert events[-1]["op"] == "delete" and events[-1]["id"] == entry.id


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


def test_format_memory_index_orders_truncates_and_sanitizes(tmp_path):
    from zenagent.agent.memory_store import format_memory_index

    store = MemoryStore(tmp_path)
    assert format_memory_index(store) == ""  # 空库 → 块消失
    first = store.write_entry(title="第一条", body="b", source="s")
    second = store.write_entry(
        title="带\n换行和一个很长很长很长很长很长很长很长很长很长很长的标题超限测试",
        body="b",
        source="s",
    )
    index = format_memory_index(store)
    lines = index.splitlines()
    assert lines[0].startswith(f"- {second.id} ")  # 最新在前
    assert lines[1] == f"- {first.id} 第一条"
    assert "\n" not in lines[0]  # 标题单行化
    assert all(len(line) <= len(f"- {second.id} ") + 80 for line in lines)

    # 65 条 → 64 行 + search 提示
    for i in range(63):
        store.write_entry(title=f"n{i}", body="b", source="s")
    index = format_memory_index(store)
    data_lines = [line for line in index.splitlines() if line.startswith("- ")]
    assert len(data_lines) == 64
    assert "memory search" in index


def test_index_skips_non_hex_stems_and_ignores_frontmatter_id(tmp_path):
    from zenagent.agent.memory_store import format_memory_index

    store = MemoryStore(tmp_path)
    entry = store.write_entry(title="正规", body="b", source="s")
    # 手放的非 4-hex 文件：索引不能展示工具读不到的条目
    stray = tmp_path / MEMORY_DIR / "entries" / "notes.md"
    stray.write_text("id: ../../evil\ntitle: 手放\n\nbody\n", encoding="utf-8")
    # frontmatter 里伪造的 id 不能盖过文件名
    forged = tmp_path / MEMORY_DIR / "entries" / f"{entry.id}.md"
    forged.write_text("id: 9999\ntitle: 正规\n\nbody\n", encoding="utf-8")

    index = format_memory_index(store)
    assert "notes" not in index and "9999" not in index and "evil" not in index
    assert f"- {entry.id} 正规" in index
    assert store.read_entry(entry.id).id == entry.id  # 文件名为准
    assert store.read_entry("notes") is None  # 非 hex stem 不可读


def test_delete_carries_reason_and_session(tmp_path):
    store = MemoryStore(tmp_path)
    entry = store.write_entry(title="t", body="b", source="s")
    assert store.delete_entry(entry.id, reason="过期", session="sess-9")
    event = store.read_journal()[-1]
    assert event["op"] == "delete" and event["reason"] == "过期"
    assert event["session"] == "sess-9"


def test_split_title_body_separates_title_and_body():
    assert split_title_body("title: 选型\n\n正文第一行\n第二行") == ("选型", "正文第一行\n第二行")


def test_split_title_body_without_title_line_returns_whole_text():
    assert split_title_body("没有标题行\n\n正文") == ("", "没有标题行\n\n正文")


def test_split_title_body_title_only_yields_empty_body():
    assert split_title_body("title: 只有标题") == ("只有标题", "")
