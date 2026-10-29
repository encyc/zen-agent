"""Append-only JSONL trace of what the agent actually did.

Unit tests prove the control flow; a trace shows the real thing: which tool was
called, with what arguments, whether it worked and how long it took. It is the
first place to look when a run "went weird", and the raw material for external
benchmark adapters later.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from zenagent.events import AgentEvent

__all__ = ["TraceRecord", "TraceRecorder"]


@dataclass
class TraceRecord:
    """One line of the trace: a turn boundary or one end of a tool call."""

    ts: float
    type: str  # "turn_start" | "tool_start" | "tool_end"
    turn: int = 0
    tool_call_id: str | None = None
    tool: str | None = None
    args: dict | None = None
    ok: bool | None = None
    duration_ms: int | None = None


class TraceRecorder:
    """Subscribable recorder; writes one JSON object per event when given a path."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.records: list[TraceRecord] = []
        self._turn = 0
        self._started_at: dict[str, float] = {}
        self._handle = None
        if path is not None:
            target = Path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            self._handle = target.open("a", encoding="utf-8")

    def __call__(self, event: AgentEvent) -> None:
        if event.type == "turn_start":
            self._turn += 1
            self._emit(TraceRecord(ts=time.time(), type="turn_start", turn=self._turn))
            return
        if event.type == "tool_execution_start":
            call_id = event.tool_call_id or ""
            self._started_at[call_id] = time.monotonic()
            self._emit(
                TraceRecord(
                    ts=time.time(),
                    type="tool_start",
                    turn=self._turn,
                    tool_call_id=call_id,
                    tool=event.tool_name,
                    args=event.args if isinstance(event.args, dict) else None,
                )
            )
            return
        if event.type == "tool_execution_end":
            call_id = event.tool_call_id or ""
            started = self._started_at.pop(call_id, None)
            duration = event.duration_ms
            if duration is None and started is not None:
                duration = round((time.monotonic() - started) * 1000)
            self._emit(
                TraceRecord(
                    ts=time.time(),
                    type="tool_end",
                    turn=self._turn,
                    tool_call_id=call_id,
                    tool=event.tool_name,
                    ok=not event.is_error,
                    duration_ms=duration,
                )
            )

    def _emit(self, record: TraceRecord) -> None:
        self.records.append(record)
        if self._handle is not None:
            self._handle.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
            self._handle.flush()

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None
