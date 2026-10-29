"""Trace recorder: pairs tool start/end, writes one JSON object per line."""

import json

from zenagent.events import AgentEvent
from zenagent.trace import TraceRecorder


def tool_event(kind: str, call_id: str, name: str, **kwargs) -> AgentEvent:
    return AgentEvent(
        type=kind,
        tool_call_id=call_id,
        tool_name=name,
        args=kwargs.get("args"),
        is_error=kwargs.get("is_error", False),
        duration_ms=kwargs.get("duration_ms"),
    )


def test_records_turn_and_tool_boundaries(tmp_path):
    path = tmp_path / "trace.jsonl"
    recorder = TraceRecorder(path)

    recorder(AgentEvent(type="turn_start"))
    recorder(tool_event("tool_execution_start", "c1", "bash", args={"command": "ls"}))
    recorder(tool_event("tool_execution_end", "c1", "bash", duration_ms=12))
    recorder.close()

    kinds = [(record.type, record.tool) for record in recorder.records]
    assert kinds == [("turn_start", None), ("tool_start", "bash"), ("tool_end", "bash")]
    assert recorder.records[1].args == {"command": "ls"}
    assert recorder.records[2].ok is True
    assert recorder.records[2].duration_ms == 12
    assert recorder.records[2].turn == 1


def test_marks_failed_tools(tmp_path):
    recorder = TraceRecorder()
    recorder(tool_event("tool_execution_start", "c1", "write_file", args={}))
    recorder(tool_event("tool_execution_end", "c1", "write_file", is_error=True))
    recorder.close()
    assert recorder.records[-1].ok is False


def test_duration_is_computed_when_the_event_omits_it():
    recorder = TraceRecorder()
    recorder(tool_event("tool_execution_start", "c1", "bash"))
    recorder(tool_event("tool_execution_end", "c1", "bash"))
    recorder.close()
    assert recorder.records[-1].duration_ms is not None


def test_written_lines_are_valid_json(tmp_path):
    path = tmp_path / "nested" / "trace.jsonl"
    recorder = TraceRecorder(path)
    recorder(AgentEvent(type="turn_start"))
    recorder(tool_event("tool_execution_start", "c1", "read_file", args={"path": "a"}))
    recorder(tool_event("tool_execution_end", "c1", "read_file"))
    recorder.close()

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    payloads = [json.loads(line) for line in lines]
    assert [p["type"] for p in payloads] == ["turn_start", "tool_start", "tool_end"]
    assert payloads[1]["args"] == {"path": "a"}


def test_in_memory_mode_does_not_write():
    recorder = TraceRecorder()
    recorder(AgentEvent(type="turn_start"))
    recorder.close()
    assert len(recorder.records) == 1
