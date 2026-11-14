"""The shared JSONL discipline: fsync on append, torn-tail repair before write.

room.jsonl had this treatment (audit-deltas v6) and nothing else did: a crash
mid-append left a half line, and the next append concatenated onto it — losing
the torn record AND the new one (audit P1-5).
"""

import json
import os
from pathlib import Path

import pytest

from zenagent.agent.jsonl import append_line, repair_tail


def test_repair_tail_truncates_only_unterminated_line(tmp_path):
    path = tmp_path / "log.jsonl"
    path.write_bytes(b'{"a": 1}\n{"b": 2}\n{"c": 3')
    repair_tail(path)
    assert path.read_bytes() == b'{"a": 1}\n{"b": 2}\n'


def test_repair_tail_leaves_complete_files_alone(tmp_path):
    path = tmp_path / "log.jsonl"
    path.write_bytes(b'{"a": 1}\n{"b": 2}\n')
    repair_tail(path)
    assert path.read_bytes() == b'{"a": 1}\n{"b": 2}\n'


@pytest.mark.parametrize("content", [b"", b'{"a": 1}'])
def test_repair_tail_handles_empty_and_never_terminated(tmp_path, content):
    path = tmp_path / "log.jsonl"
    path.write_bytes(content)
    repair_tail(path)
    assert path.read_bytes() == b""


def test_repair_tail_missing_file_is_a_noop(tmp_path):
    repair_tail(tmp_path / "absent.jsonl")  # must not raise


def test_append_line_fsyncs(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(os, "fsync", lambda fd: calls.append(fd))
    append_line(tmp_path / "log.jsonl", '{"a": 1}')
    assert calls, "append_line must fsync before returning"


def test_append_line_repairs_a_torn_tail_before_appending(tmp_path):
    path = tmp_path / "log.jsonl"
    path.write_bytes(b'{"a": 1}\n{"b": 2')
    append_line(path, '{"c": 3}')
    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert records == [{"a": 1}, {"c": 3}]


# --- the consumers that now share the discipline ---------------------------

def test_session_append_survives_a_torn_tail(tmp_path):
    """A crash mid-append must not swallow the record written after restart."""
    from zenagent.agent.session import Session

    path = tmp_path / "s.jsonl"
    session = Session(path)
    session._write_line({"kind": "header", "v": 1})
    session._write_line({"kind": "meta", "i": 2})
    with path.open("ab") as handle:
        handle.write(b'{"kind": "to')  # the torn tail a hard kill leaves
    session._write_line({"kind": "meta", "i": 3})

    lines = path.read_text().splitlines()
    assert len(lines) == 3
    for line in lines:
        json.loads(line)  # no merged garbage: the torn tail was repaired


def test_run_ledger_append_survives_a_torn_tail(tmp_path):
    from zenagent.app.runs import RunLedger, RunRecord

    path = tmp_path / "runs.jsonl"
    ledger = RunLedger(path)
    ledger._append(RunRecord(id="r1", kind="agent", status="running", started_at=1, updated_at=1))
    with path.open("ab") as handle:
        handle.write(b'{"id": "r2", "stat')
    ledger._append(RunRecord(id="r3", kind="agent", status="running", started_at=3, updated_at=3))

    # The torn tail was never a complete record: it is repaired away, r3 lands.
    lines = path.read_text().splitlines()
    assert [json.loads(line)["id"] for line in lines] == ["r1", "r3"]


def test_room_append_line_survives_a_torn_tail(tmp_path):
    """Covers work_items.jsonl too: it writes through the same store path."""
    from zenagent.app.swarm.room import SwarmStore

    path = tmp_path / "work_items.jsonl"
    store = SwarmStore(tmp_path)
    store._append_line(path, {"id": "w1", "state": "open"})
    with path.open("ab") as handle:
        handle.write(b'{"id": "w2", "sta')
    store._append_line(path, {"id": "w3", "state": "open"})

    lines = path.read_text().splitlines()
    assert [json.loads(line)["id"] for line in lines] == ["w1", "w3"]
