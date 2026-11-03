"""SubagentTracker unit contract: live meta, spend accounting, disk cache reset."""

from __future__ import annotations

import time

from zenagent.app.subagent_tracker import SubagentTracker
from zenagent.app.views import ToolCallView


def _task_card(call_id: str = "call-1", status: str = "running") -> ToolCallView:
    return ToolCallView(
        call_id=call_id,
        name="task",
        args_preview="",
        args={"name": "search"},
        status=status,  # type: ignore[arg-type]
        started_at=time.monotonic(),
    )


def test_start_makes_a_running_row_and_end_records_meta():
    tracker = SubagentTracker(notify=lambda: None)
    tracker.on_event("call-1", "start", {"name": "search"})
    views = tracker.views([_task_card()], session_path=None)
    assert [v.status for v in views] == ["running"]
    assert views[0].has_transcript

    tracker.on_event(
        "call-1",
        "end",
        {"status": "ok", "duration_ms": 120, "cost": 0.25, "tokens": 90},
    )
    # With no session file the disk-sidecar meta is empty, so the row falls
    # back to the live card's envelope — the end event's meta is bookkeeping.
    views = tracker.views([_task_card(status="ok")], session_path=None)
    assert [v.status for v in views] == ["ok"]
    assert views[0].duration_ms == 0


def test_add_spend_accumulates_and_reset_clears_everything():
    notices: list[int] = []
    tracker = SubagentTracker(notify=lambda: notices.append(1))
    tracker.on_event("call-1", "start", {"name": "search"})
    tracker.add_spend(0.5, 200)
    assert tracker.cost == 0.5
    assert tracker.tokens == 200

    tracker.reset()
    assert tracker.cost == 0.0
    assert tracker.tokens == 0
    assert tracker.transcript("call-1", session_path=None) is None
    views = tracker.views([_task_card()], session_path=None)
    # Live card without meta falls back to the envelope status ("running").
    assert views[0].status == "running"


def test_notify_fires_on_start_and_end():
    calls: list[str] = []
    tracker = SubagentTracker(notify=lambda: calls.append("n"))
    tracker.on_event("call-1", "start", {"name": "x"})
    tracker.on_event("call-1", "end", {"status": "ok"})
    assert calls == ["n", "n"]
