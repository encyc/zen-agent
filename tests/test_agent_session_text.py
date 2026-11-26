import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from zenagent.agent.session_text import (
    LEVELS,
    Turn,
    count_messages,
    excerpt,
    iter_turns,
)


def write_jsonl(path: Path, lines: list[dict]) -> Path:
    path.write_text(
        "".join(json.dumps(o, ensure_ascii=False) + "\n" for o in lines),
        encoding="utf-8",
    )
    return path


def user(text: str) -> dict:
    return {"role": "user", "content": text, "timestamp": 1}


def assistant(*blocks: dict) -> dict:
    return {"role": "assistant", "content": list(blocks), "timestamp": 2}


def tool_result(text: str, name: str = "bash") -> dict:
    return {
        "role": "toolResult",
        "tool_call_id": "c1",
        "tool_name": name,
        "content": [{"type": "text", "text": text}],
        "is_error": False,
        "timestamp": 3,
    }


def test_conversation_level_reads_user_and_assistant_text(tmp_path):
    path = write_jsonl(
        tmp_path / "s.jsonl",
        [
            {"type": "session", "version": 1, "id": "s"},
            user("find the permission gate"),
            assistant({"type": "text", "text": "it lives in app/safety.py"}),
        ],
    )
    turns = list(iter_turns(path, "conversation"))
    assert [(t.index, t.role, t.kind) for t in turns] == [
        (0, "user", "text"),
        (1, "assistant", "text"),
    ]
    assert "app/safety.py" in turns[1].text


def test_thinking_is_never_returned_at_any_level(tmp_path):
    path = write_jsonl(
        tmp_path / "s.jsonl",
        [
            assistant(
                {"type": "thinking", "thinking": "SECRET-REASONING"},
                {"type": "text", "text": "visible answer"},
            )
        ],
    )
    for level in LEVELS:
        texts = [t.text for t in iter_turns(path, level)]
        assert all("SECRET-REASONING" not in text for text in texts), level
        assert any("visible answer" in text for text in texts), level


def test_tools_level_adds_tool_calls_with_their_name_and_arguments(tmp_path):
    path = write_jsonl(
        tmp_path / "s.jsonl",
        [
            assistant(
                {
                    "type": "toolCall",
                    "id": "c1",
                    "name": "edit_file",
                    "arguments": {"path": "zenagent/app/permissions.py"},
                }
            )
        ],
    )
    assert list(iter_turns(path, "conversation")) == []
    turns = list(iter_turns(path, "tools"))
    assert len(turns) == 1
    assert turns[0].kind == "toolCall"
    assert turns[0].tool_name == "edit_file"
    assert "zenagent/app/permissions.py" in turns[0].text


def test_all_level_adds_tool_result_bodies_only(tmp_path):
    path = write_jsonl(
        tmp_path / "s.jsonl",
        [tool_result("Traceback: KeyError 'flavour'")],
    )
    assert list(iter_turns(path, "tools")) == []
    turns = list(iter_turns(path, "all"))
    assert len(turns) == 1
    assert turns[0].kind == "result"
    assert turns[0].tool_name == "bash"
    assert "KeyError" in turns[0].text


def test_message_index_counts_messages_not_side_channel_records(tmp_path):
    path = write_jsonl(
        tmp_path / "s.jsonl",
        [
            {"type": "session", "version": 1, "id": "s"},
            {"type": "run_start", "timestamp": 1},
            user("first"),
            {"type": "tool_end", "timestamp": 2, "call_id": "c1"},
            {"type": "goal", "timestamp": 3, "objective": "x"},
            assistant({"type": "text", "text": "second"}),
        ],
    )
    assert [(t.index, t.text[:6]) for t in iter_turns(path, "conversation")] == [
        (0, "first"),
        (1, "second"),
    ]


def test_count_messages_ignores_side_channel_records(tmp_path):
    path = write_jsonl(
        tmp_path / "s.jsonl",
        [
            {"type": "session", "version": 1, "id": "s"},
            user("first"),
            {"type": "run_end", "timestamp": 2},
            assistant({"type": "toolCall", "id": "c1", "name": "bash", "arguments": {}}),
            {"type": "goal", "timestamp": 3, "objective": "x"},
        ],
    )
    # Two message lines, whatever include would render.
    assert count_messages(path) == 2


def test_unreadable_lines_are_skipped_not_fatal(tmp_path):
    path = tmp_path / "s.jsonl"
    path.write_text(
        json.dumps(user("keep me"), ensure_ascii=False) + "\n"
        + '{"role": "user", "content": "torn\n'
        + json.dumps(user("and me"), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    assert [t.text for t in iter_turns(path, "conversation")] == ["keep me", "and me"]


def test_unknown_include_level_raises_value_error(tmp_path):
    path = write_jsonl(tmp_path / "s.jsonl", [user("x")])
    with pytest.raises(ValueError, match="include"):
        list(iter_turns(path, "everything"))


def test_turn_is_frozen_and_tool_name_defaults_to_empty():
    turn = Turn(index=3, role="user", kind="text", text="hi")
    assert turn.tool_name == ""
    with pytest.raises(FrozenInstanceError):
        turn.text = "changed"  # type: ignore[misc]


def test_excerpt_windows_around_the_match_and_flattens_whitespace():
    text = "a\n\n  b   " + "x" * 40 + "NEEDLE" + "y" * 200
    window = excerpt(text, text.index("NEEDLE"), text.index("NEEDLE") + 6)
    assert "NEEDLE" in window
    assert "\n" not in window
    assert len(window) < len(text)
    assert window.startswith("a b")
