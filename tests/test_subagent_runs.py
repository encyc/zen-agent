"""Subagent children reach the shared run ledger."""

from __future__ import annotations

from types import SimpleNamespace

from zenagent.app.runs import RunLedger
from zenagent.app.subagent_runs import SubagentRunSink


def _runtime(tmp_path, run_id="r_parent"):
    return SimpleNamespace(
        run_ledger=RunLedger(tmp_path / "runs.jsonl"),
        session=SimpleNamespace(path=tmp_path / "parent.jsonl"),
        current_run_id=run_id,
    )


def test_start_records_parent_and_session(tmp_path):
    runtime = _runtime(tmp_path)
    record = SubagentRunSink(runtime).start("review")

    assert record is not None
    [row] = runtime.run_ledger.load()
    assert row.id == record.id
    assert row.kind == "subagent"
    assert row.status == "running"
    assert row.agent_id == "review"
    assert row.session == str(runtime.session.path)
    assert row.parent_run_id == "r_parent"


def test_finish_maps_the_child_status(tmp_path):
    runtime = _runtime(tmp_path)
    sink = SubagentRunSink(runtime)
    record = sink.start("review")
    sink.finish(
        record,
        {
            "status": "ok",
            "termination": "stop",
            "turns": 2,
            "cost": 0.25,
            "duration_ms": 1200,
        },
        "",
    )

    [row] = runtime.run_ledger.load()
    assert row.status == "done"
    assert row.termination == "stop"
    assert row.turns == 2
    assert row.cost == 0.25
    assert row.duration_ms == 1200


def test_finish_maps_truncation_to_failed(tmp_path):
    runtime = _runtime(tmp_path)
    sink = SubagentRunSink(runtime)
    record = sink.start("review")
    sink.finish(
        record,
        {"status": "truncated", "reason": "timeout", "error": "too slow"},
        "",
    )

    [row] = runtime.run_ledger.load()
    assert row.status == "failed"
    assert row.termination == "timeout"
    assert row.error == "too slow"


def test_finish_without_details_is_failed_or_aborted(tmp_path):
    runtime = _runtime(tmp_path)
    sink = SubagentRunSink(runtime)
    failed = sink.start("failed")
    sink.finish(failed, None, "RuntimeError: boom")
    aborted = sink.start("aborted")
    sink.finish(aborted, None, "")
    rows = {row.agent_id: row for row in runtime.run_ledger.load()}
    assert rows["failed"].status == "failed"
    assert "boom" in rows["failed"].error
    assert rows["aborted"].status == "aborted"


def test_start_without_a_ledger_is_a_noop(tmp_path):
    runtime = SimpleNamespace(
        run_ledger=None,
        session=SimpleNamespace(path=tmp_path / "parent.jsonl"),
        current_run_id=None,
    )
    sink = SubagentRunSink(runtime)
    assert sink.start("review") is None
    sink.finish(None, None, "")
