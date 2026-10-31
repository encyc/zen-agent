"""Opt-in run metrics.

One JSON line per run: counts, tokens and spend. Deliberately no prompt or model
output text — metrics are for aggregate reporting, not for recording content.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from zenagent.events import AgentEvent
from zenagent.types import AssistantMessage, Usage

__all__ = ["MetricsRecorder", "RunMetrics", "append_metrics"]


@dataclass
class RunMetrics:
    run_id: str
    model: str
    started_at: float
    ended_at: float
    turns: int
    tool_calls: int
    input: int
    output: int
    cache_read: int
    cache_write: int
    cost: float
    termination: str | None
    #: Per-tool ``{calls, errors, ms}`` from ``agent_end.tool_stats``.
    tools: dict = field(default_factory=dict)

    @property
    def duration_ms(self) -> int:
        return round((self.ended_at - self.started_at) * 1000)


class MetricsRecorder:
    """Subscribe to agent events and build one RunMetrics per run."""

    def __init__(self, model: str = "") -> None:
        self.model = model
        self.run_id = uuid.uuid4().hex[:12]
        self.started_at = time.time()
        self.turns = 0
        self.tool_calls = 0
        self.tool_stats: dict = {}
        self.usage = Usage()

    def __call__(self, event: AgentEvent) -> None:
        if event.type == "turn_start":
            self.turns += 1
        elif event.type == "tool_execution_start":
            self.tool_calls += 1
        elif event.type == "agent_end" and event.tool_stats:
            self.tool_stats = dict(event.tool_stats.get("tools") or {})
        elif event.type == "message_end" and isinstance(event.message, AssistantMessage):
            usage = event.message.usage
            self.usage.input += usage.input
            self.usage.output += usage.output
            self.usage.cache_read += usage.cache_read
            self.usage.cache_write += usage.cache_write
            self.usage.cost.total += usage.cost.total

    def build(self, termination: str | None) -> RunMetrics:
        return RunMetrics(
            run_id=self.run_id,
            model=self.model,
            started_at=self.started_at,
            ended_at=time.time(),
            turns=self.turns,
            tool_calls=self.tool_calls,
            input=self.usage.input,
            output=self.usage.output,
            cache_read=self.usage.cache_read,
            cache_write=self.usage.cache_write,
            cost=self.usage.cost.total,
            termination=termination,
            tools=dict(self.tool_stats),
        )


def append_metrics(path: Path | str, metrics: RunMetrics) -> Path:
    """Append one metric line (creating parents) and return the path."""
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(asdict(metrics), ensure_ascii=False))
        handle.write("\n")
    return target
