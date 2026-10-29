"""Helpers for behavioural eval runs: reports and baseline comparison.

The runner itself stays in ``scripts/eval.py`` (it needs a live model and a key);
this module holds the pure pieces so they are unit-testable and reusable by
``zenagent evals``.
"""

from __future__ import annotations

import json
from pathlib import Path

__all__ = ["compare_reports", "load_report", "render_comparison", "write_report"]


def load_report(path: Path | str) -> dict:
    """Load a JSON report produced by ``scripts/eval.py --report``."""
    target = Path(path)
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(
            f"cannot read eval report {target}: {exc}"
        ) from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
        raise ValueError(  # noqa: TRY004 - malformed report is an error, not a type
            f"eval report {target} has no records"
        )
    return payload


def write_report(path: Path | str, report: dict) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def compare_reports(baseline: dict, current: dict) -> dict:
    """Diff two reports by case id: regressions, fixes, added/removed cases."""
    base = {record["id"]: record for record in baseline.get("records", [])}
    curr = {record["id"]: record for record in current.get("records", [])}
    regressions = [
        case_id
        for case_id in curr
        if case_id in base and base[case_id].get("ok") and not curr[case_id].get("ok")
    ]
    fixes = [
        case_id
        for case_id in curr
        if case_id in base and not base[case_id].get("ok") and curr[case_id].get("ok")
    ]
    return {
        "regressions": regressions,
        "fixes": fixes,
        "added": [case_id for case_id in curr if case_id not in base],
        "removed": [case_id for case_id in base if case_id not in curr],
        "baseline_pass_rate": baseline.get("pass_rate", 0.0),
        "current_pass_rate": current.get("pass_rate", 0.0),
        "baseline_cost": baseline.get("cost", 0.0),
        "current_cost": current.get("cost", 0.0),
    }


def render_comparison(comparison: dict) -> str:
    lines = [
        (
            f"baseline pass rate {comparison['baseline_pass_rate']:.0%} -> "
            f"{comparison['current_pass_rate']:.0%} "
            f"(cost ${comparison['baseline_cost']:.4f} -> ${comparison['current_cost']:.4f})"
        )
    ]
    for label, key in (
        ("regressions", "regressions"),
        ("fixes", "fixes"),
        ("new cases", "added"),
        ("removed cases", "removed"),
    ):
        if comparison[key]:
            lines.append(f"{label}: {', '.join(comparison[key])}")
    if not comparison["regressions"] and not comparison["fixes"]:
        lines.append("no status changes")
    return "\n".join(lines)
