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


def test_tracker_shows_running_for_inflight_background_task(tmp_path):
    import asyncio

    from zenagent.agent.subagents import SubagentStore
    from zenagent.app.subagent_tracker import SubagentTracker
    from zenagent.tools.task import close_task_manager, get_task_manager

    async def scenario():
        # A background child pinned to this session has written only the
        # transcript header so far: the disk meta reads "interrupted", but the
        # TaskManager knows the run is alive and the row must say running.
        session = tmp_path / "s.jsonl"
        SubagentStore(session).begin("call-1", "Research", "m")

        manager = get_task_manager()
        gate = asyncio.Event()

        def factory():
            async def run():
                await gate.wait()
                return "e", {}

            return run

        run = manager.start("call-1", "Research", "m", factory)

        tracker = SubagentTracker(notify=lambda: None)
        tracker.on_event("call-1", "start", {"name": "Research"})
        card = _task_card(status="ok")  # the dispatch call itself has completed
        views = tracker.views([card], session_path=session)
        assert [v.status for v in views] == ["running"]

        gate.set()
        await manager.wait(run.task_id, timeout_s=5)
        # The recorder's finish hook writes the end line and emits the end
        # event to the tracker — same contract as the sync path.
        SubagentStore(session).finish(
            "call-1", status="ok", duration_ms=5, cost=0.0, tokens=5
        )
        tracker.on_event(
            "call-1", "end", {"status": "ok", "duration_ms": 5, "cost": 0.0, "tokens": 5}
        )
        views = tracker.views([card], session_path=session)
        assert [v.status for v in views] == ["ok"]
        await close_task_manager()

    asyncio.run(scenario())
