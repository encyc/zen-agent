"""The worker transcript peek: the tail of a session JSONL, board-shaped."""

from __future__ import annotations

import json
from pathlib import Path

from zenagent.app.taiji.transcript import peek_tail


def write_lines(path: Path, lines: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines),
        encoding="utf-8",
    )


def test_peek_tail_returns_the_last_user_and_assistant_texts() -> None:
    path = Path("/tmp") / "taiji-peek" / "full.jsonl"
    write_lines(
        path,
        [
            {"type": "session", "id": "s"},
            {"role": "user", "content": "fix the bug"},
            {"role": "assistant", "content": [{"type": "text", "text": "on it"}]},
            {"role": "tool", "content": [{"type": "tool_use", "id": "t"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "done"}]},
        ],
    )

    items = peek_tail(str(path), limit=3)

    assert items == [
        {"role": "user", "text": "fix the bug"},
        {"role": "assistant", "text": "on it"},
        {"role": "assistant", "text": "done"},
    ]


def test_peek_tail_skips_torn_and_roleless_lines() -> None:
    path = Path("/tmp") / "taiji-peek" / "torn.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    good = json.dumps({"role": "user", "content": "hi"}, ensure_ascii=False)
    path.write_text(
        good + "\n" + '{"role": "assistant", "content": [{"type": "text", "te'
        + "\n" + '{"type": "meta", "note": "no role"}' + "\n",
        encoding="utf-8",
    )

    assert peek_tail(str(path), limit=10) == [{"role": "user", "text": "hi"}]


def test_peek_tail_on_missing_and_empty_files() -> None:
    assert peek_tail("/tmp/taiji-peek/absent.jsonl") == []
    empty = Path("/tmp/taiji-peek/empty.jsonl")
    empty.write_text("", encoding="utf-8")
    assert peek_tail(str(empty)) == []


def test_peek_tail_handles_string_content() -> None:
    path = Path("/tmp/taiji-peek/string.jsonl")
    write_lines(path, [{"role": "assistant", "content": "plain text"}])

    assert peek_tail(str(path)) == [{"role": "assistant", "text": "plain text"}]
