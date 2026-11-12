"""Task transcripts: one run sliced out of the role's own session file.

A role appends every run to one session; the injected prompt is the marker
that turns the flat file into per-task ranges, so the room can replay what
an agent did without carrying a second copy of the transcript.
"""

from __future__ import annotations

import json
from pathlib import Path

from zenagent.app.swarm.protocol import injected_prompt
from zenagent.app.swarm.room import RoomMessage, TaskRecord
from zenagent.app.swarm.transcript import (
    read_task_transcript,
    read_task_transcript_from_disk,
)
from zenagent.types import (
    AssistantMessage,
    TextContent,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
)


def _task(task_id: str) -> TaskRecord:
    return TaskRecord(
        id=task_id,
        assignee="research",
        state="done",
        source_message_id="m_1",
        chain_id="c_1",
        chain_depth=1,
    )


def _source(task_id: str) -> RoomMessage:
    return RoomMessage(
        id="m_1",
        ts=0,
        author_kind="user",
        author_id="you",
        text="查 X",
        mentions=["research"],
    )


def _assistant(*content) -> AssistantMessage:
    return AssistantMessage(
        content=list(content),
        provider="p",
        model="m",
        usage=Usage(),
        stop_reason="stop",
        timestamp=0,
    )


def _write_session(path: Path, *messages) -> None:
    lines = [json.dumps({"type": "session", "version": 1})]
    lines.extend(json.dumps(message.to_json()) for message in messages)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _run(task_id: str, text: str) -> UserMessage:
    return UserMessage(
        content=injected_prompt(_source(task_id), _task(task_id)), timestamp=0
    )


def test_slices_only_the_run_named_by_the_injected_prompt(tmp_path):
    path = tmp_path / "session.jsonl"
    _write_session(
        path,
        _run("t_1", "查 X"),
        _assistant(
            TextContent(text="先读技能"),
            ToolCall(
                id="call_1", name="read_skill", arguments={"name": "risk-policy-expert"}
            ),
        ),
        ToolResultMessage(
            tool_call_id="call_1",
            tool_name="read_skill",
            content=[TextContent(text="skill body")],
            is_error=False,
            timestamp=0,
        ),
        _assistant(TextContent(text="结论是 Z")),
        _run("t_2", "查 Y"),
        _assistant(TextContent(text="第二个结论")),
    )

    available, rows = read_task_transcript(path, "t_1")

    assert available is True
    assert [row["kind"] for row in rows] == ["assistant", "tool", "assistant"]
    assert rows[0]["text"] == "先读技能"
    assert rows[1]["name"] == "read_skill"
    assert rows[1]["args"] == {"name": "risk-policy-expert"}
    assert '"risk-policy-expert"' in rows[1]["args_preview"]
    assert rows[1]["result_text"] == "skill body"
    assert rows[1]["status"] == "ok"
    assert rows[2]["text"] == "结论是 Z"
    assert all("查 X" not in json.dumps(row) for row in rows)
    assert all("第二个结论" not in json.dumps(row) for row in rows)


def test_a_task_without_its_injected_prompt_reports_no_record(tmp_path):
    path = tmp_path / "session.jsonl"
    _write_session(path, _assistant(TextContent(text="别的 run")))

    available, rows = read_task_transcript(path, "t_1")

    assert available is False
    assert rows == []


def test_an_in_flight_call_is_running_until_the_task_settles(tmp_path):
    path = tmp_path / "session.jsonl"
    _write_session(
        path,
        _run("t_1", "查 X"),
        _assistant(ToolCall(id="call_1", name="bash", arguments={"command": "ls"})),
    )

    _, live = read_task_transcript(path, "t_1", running=True)
    _, settled = read_task_transcript(path, "t_1", running=False)

    assert [row["kind"] for row in live] == ["tool"]
    assert live[0]["status"] == "running"
    assert settled[0]["status"] == "interrupted"


def test_full_views_ship_so_the_coding_renderer_can_replay_them(tmp_path):
    path = tmp_path / "session.jsonl"
    _write_session(
        path,
        _run("t_1", "查 X"),
        _assistant(
            TextContent(text="读大文件"),
            ToolCall(id="call_1", name="bash", arguments={"command": "cat big"}),
        ),
        ToolResultMessage(
            tool_call_id="call_1",
            tool_name="bash",
            content=[TextContent(text="x" * 5000)],
            is_error=False,
            timestamp=0,
        ),
    )

    _, rows = read_task_transcript(path, "t_1")

    # Unlike the trimmed room row, the full view keeps the raw result and the
    # parsed arguments so ToolRowView renders exactly what coding mode shows.
    assert rows[1]["kind"] == "tool"
    assert rows[1]["result_text"] == "x" * 5000
    assert rows[1]["args"] == {"command": "cat big"}
    assert rows[1]["call_id"] == "call_1"
    assert rows[0]["model"] == "m"


def test_disk_resolver_scans_every_session_file_for_the_task(tmp_path):
    sessions = tmp_path / "agents" / "research" / "sessions"
    sessions.mkdir(parents=True)
    _write_session(
        sessions / "a.jsonl",
        _run("t_1", "查 X"),
        _assistant(TextContent(text="第一个结论")),
    )
    _write_session(
        sessions / "b.jsonl",
        _run("t_2", "查 Y"),
        _assistant(TextContent(text="第二个结论")),
    )

    rows = read_task_transcript_from_disk(sessions, "t_2")

    assert rows is not None
    assert any(row.get("text") == "第二个结论" for row in rows)
    assert all("第一个结论" not in json.dumps(row) for row in rows)


def test_disk_resolver_returns_none_when_the_task_is_absent(tmp_path):
    assert read_task_transcript_from_disk(tmp_path, "t_nope") is None
