"""Aggregate token usage and spend across saved sessions.

Read-only and tolerant: a torn line or a session from before usage was recorded
must not break the report.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from pyagent.types import AssistantMessage, message_from_json

__all__ = ["ModelUsage", "UsageReport", "collect_usage", "render_report"]


@dataclass
class ModelUsage:
    model: str
    calls: int = 0
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    cost: float = 0.0

    def add(self, message: AssistantMessage) -> None:
        usage = message.usage
        self.calls += 1
        self.input += usage.input
        self.output += usage.output
        self.cache_read += usage.cache_read
        self.cache_write += usage.cache_write
        self.cost += usage.cost.total


@dataclass
class UsageReport:
    per_model: dict[str, ModelUsage] = field(default_factory=dict)
    sessions: int = 0

    @property
    def total_cost(self) -> float:
        return sum(usage.cost for usage in self.per_model.values())

    @property
    def total_tokens(self) -> int:
        return sum(
            usage.input + usage.output + usage.cache_read + usage.cache_write
            for usage in self.per_model.values()
        )

    def to_json(self) -> dict:
        return {
            "sessions": self.sessions,
            "total_cost": self.total_cost,
            "total_tokens": self.total_tokens,
            "models": {
                model: {
                    "calls": usage.calls,
                    "input": usage.input,
                    "output": usage.output,
                    "cache_read": usage.cache_read,
                    "cache_write": usage.cache_write,
                    "cost": usage.cost,
                }
                for model, usage in self.per_model.items()
            },
        }


def collect_usage(paths: list[Path]) -> UsageReport:
    """Sum assistant usage from the given session files."""
    report = UsageReport()
    for path in paths:
        try:
            lines = Path(path).read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        counted = False
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                message = message_from_json(json.loads(line))
            except (ValueError, TypeError):
                continue  # tolerate torn or legacy lines
            if not isinstance(message, AssistantMessage):
                continue
            usage = report.per_model.setdefault(
                message.model or "unknown", ModelUsage(model=message.model or "unknown")
            )
            usage.add(message)
            counted = True
        if counted:
            report.sessions += 1
    return report


def session_paths(since: str | None = None) -> list[Path]:
    """Session files, optionally filtered by mtime >= ``YYYY-MM-DD``."""
    from pyagent.agent.session import sessions_dir

    cutoff = None
    if since:
        cutoff = datetime.strptime(since, "%Y-%m-%d").timestamp()  # raises on bad input
    directory = sessions_dir()
    if not directory.is_dir():
        return []
    paths = []
    for path in sorted(directory.glob("*.jsonl")):
        if cutoff is not None and path.stat().st_mtime < cutoff:
            continue
        paths.append(path)
    return paths


def render_report(report: UsageReport) -> str:
    """Human-readable table."""
    if not report.per_model:
        return "no usage recorded"
    header = f"{'model':<24}{'calls':>7}{'input':>9}{'output':>9}{'cache_r':>9}{'cache_w':>9}{'cost':>10}"
    rows = [header, "-" * len(header)]
    for model, usage in sorted(report.per_model.items(), key=lambda item: -item[1].cost):
        rows.append(
            f"{model:<24}{usage.calls:>7}{usage.input:>9}{usage.output:>9}"
            f"{usage.cache_read:>9}{usage.cache_write:>9}{usage.cost:>10.4f}"
        )
    rows.append("-" * len(header))
    rows.append(
        f"{'total':<24}{sum(u.calls for u in report.per_model.values()):>7}"
        f"{sum(u.input for u in report.per_model.values()):>9}"
        f"{sum(u.output for u in report.per_model.values()):>9}"
        f"{sum(u.cache_read for u in report.per_model.values()):>9}"
        f"{sum(u.cache_write for u in report.per_model.values()):>9}"
        f"{report.total_cost:>10.4f}"
    )
    rows.append(f"{report.sessions} session(s)")
    return "\n".join(rows)
