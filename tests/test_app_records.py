"""The session record vocabulary: one shape per kind, and one predicate."""

from __future__ import annotations

from zenagent.app import records


def test_tool_end_shape_is_exactly_todays():
    assert records.tool_end("c1", "bash", 12, False, None) == {
        "type": "tool_end",
        "call_id": "c1",
        "tool_name": "bash",
        "is_error": False,
        "duration_ms": 12,
    }


def test_tool_end_omits_absent_fields():
    assert records.tool_end("c1", "bash", None, True, None) == {
        "type": "tool_end",
        "call_id": "c1",
        "tool_name": "bash",
        "is_error": True,
    }


def test_compaction_shape():
    assert records.compaction(1200, "auto") == {
        "type": "compaction",
        "tokensBefore": 1200,
        "reason": "auto",
    }


def test_run_boundary_shapes():
    assert records.run_start() == {"type": "run_start"}
    assert records.run_end(termination="stop", turns=2) == {
        "type": "run_end",
        "termination": "stop",
        "turns": 2,
    }
    assert records.run_end(
        termination="error", turns=1, duration_ms=42, detail="boom"
    ) == {
        "type": "run_end",
        "termination": "error",
        "turns": 1,
        "duration_ms": 42,
        "detail": "boom",
    }


def test_retry_and_mode_shapes():
    assert records.retry(
        attempt=1, max_retries=3, delay_ms=500, error="503", model="deepseek-flash"
    ) == {
        "type": "retry",
        "attempt": 1,
        "max_retries": 3,
        "delay_ms": 500,
        "error": "503",
        "model": "deepseek-flash",
    }
    assert records.mode("plan") == {"type": "mode", "mode": "plan"}


def test_predicates_only_recognize_their_own_kind():
    end = records.tool_end("c1", "bash", 1, False, None)
    compacted = records.compaction(1, "manual")
    assert records.is_tool_end(end) and not records.is_tool_end(compacted)
    assert records.is_compaction(compacted) and not records.is_compaction(end)
    assert records.record_type(end) == "tool_end"
    assert records.record_type({}) is None


def test_keep_for_rewrite_carries_compaction_and_tool_end_only():
    keep = records.keep_for_rewrite()
    assert keep({"type": records.COMPACTION})
    assert keep({"type": records.TOOL_END, "call_id": "c1"})
    for kind in (records.RUN_START, records.RUN_END, records.RETRY, records.MODE):
        assert not keep({"type": kind})
    assert not keep({"type": "future-kind"})


def test_keep_for_rewrite_can_drop_tool_end():
    keep = records.keep_for_rewrite(keep_tool_end=False)
    assert keep({"type": records.COMPACTION})
    assert not keep({"type": records.TOOL_END, "call_id": "c1"})


def test_details_are_bounded():
    diff = "\n".join(f"+line {index}" for index in range(records.TOOL_DIFF_MAX_LINES + 50))
    record = records.tool_end("c1", "bash", 1, False, {"diff": diff, "exit_code": 0})
    details = record["details"]
    assert details["diff"].count("\n") == records.TOOL_DIFF_MAX_LINES - 1
    assert details["diff_truncated"] is True
    assert details["exit_code"] == 0


def test_huge_details_are_capped():
    details = {"blob": "x" * (records.TOOL_DETAILS_LIMIT * 2), "exit_code": 0}
    record = records.tool_end("c1", "bash", 1, False, details)
    assert len(str(record["details"])) <= records.TOOL_DETAILS_LIMIT * 2
    assert record["details"]["details_truncated"] is True
