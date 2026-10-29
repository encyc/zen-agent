"""Eval reports: loading, writing, baseline comparison, and CLI delegation."""

import json

import pytest

from zenagent.evals_runner import (
    compare_reports,
    load_report,
    render_comparison,
    write_report,
)


def report(*entries: tuple[str, bool], **extra) -> dict:
    records = [{"id": case_id, "ok": ok} for case_id, ok in entries]
    return {
        "pass_rate": sum(ok for _, ok in entries) / len(entries) if entries else 0.0,
        "cost": 0.01,
        "records": records,
        **extra,
    }


def test_compare_reports_classifies_regressions_and_fixes():
    baseline = report(("a", True), ("b", False), ("c", True))
    current = report(("a", False), ("b", True), ("c", True), ("d", True))
    comparison = compare_reports(baseline, current)
    assert comparison["regressions"] == ["a"]
    assert comparison["fixes"] == ["b"]
    assert comparison["added"] == ["d"]
    assert comparison["removed"] == []
    text = render_comparison(comparison)
    assert "regressions: a" in text and "fixes: b" in text


def test_compare_reports_reports_removed_cases():
    comparison = compare_reports(report(("a", True), ("b", True)), report(("a", True)))
    assert comparison["removed"] == ["b"]


def test_compare_reports_with_no_changes():
    comparison = compare_reports(report(("a", True)), report(("a", True)))
    assert comparison["regressions"] == []
    assert "no status changes" in render_comparison(comparison)


def test_report_roundtrip(tmp_path):
    path = tmp_path / "reports" / "run.json"
    write_report(path, {"records": [{"id": "a", "ok": True}]})
    assert load_report(path)["records"][0]["id"] == "a"


def test_load_report_rejects_bad_files(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(ValueError, match="cannot read eval report"):
        load_report(bad)
    no_records = tmp_path / "empty.json"
    no_records.write_text(json.dumps({"pass_rate": 1.0}))
    with pytest.raises(ValueError, match="no records"):
        load_report(no_records)


def test_cli_evals_list_delegates(capsys):
    import zenagent.cli.main as cli_main

    assert cli_main.main(["evals", "--list"]) == 0
    out = capsys.readouterr().out
    assert "skill_use" in out and "mcp_tool" in out
