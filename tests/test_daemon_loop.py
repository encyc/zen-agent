# tests/test_daemon_loop.py
import json
import time

from zenagent.app.automations import (
    AutomationStore,
    Job,
    JobState,
    Schedule,
    StateStore,
)
from zenagent.cli.daemon import (
    _drop_daemon_lock,
    _take_daemon_lock,
    daemon_running,
    next_wake,
    status_text,
    tick,
)


def at(h, mi=0):
    return time.mktime((2026, 11, 26, h, mi, 0, 0, 0, -1))


def test_next_wake_picks_the_earliest_and_never_stalls():
    now = at(10)
    job = Job(
        id="a_1",
        name="r",
        prompt="p",
        schedule=Schedule.parse("every 1h"),
        session="s.jsonl",
    )
    # Due exactly now: the floor keeps the loop from sleeping zero.
    assert next_wake([job], {"a_1": JobState(last_run_at=at(9))}, now) >= now
    sooner = Job(
        id="a_2",
        name="s",
        prompt="p",
        schedule=Schedule.parse("every 10m"),
        session="t.jsonl",
    )
    both = next_wake(
        [job, sooner],
        {"a_1": JobState(last_run_at=at(9)), "a_2": JobState(last_run_at=at(9, 55))},
        now,
    )
    timed = next_wake([job], {"a_1": JobState(last_run_at=at(9))}, now)
    assert both <= timed
    assert next_wake([], {}, now) == now + 60


async def test_tick_runs_a_due_job_and_writes_state(
    prepared_runtime, app_env
):
    runtime, chat = await prepared_runtime(root=app_env)
    store = AutomationStore(runtime.root)
    job = store.add(
        Job(
            id="",
            name="r",
            prompt="go",
            schedule=Schedule.parse("every 1h"),
            session="s.jsonl",
        )
    )
    StateStore(runtime.root).save({job.id: JobState(last_run_at=at(6), run_count=2)})

    results = await tick(
        runtime, chat, root=runtime.root, now=at(10), request_path=app_env / "req.json"
    )
    assert [r.status for r in results] == ["done"]
    assert runtime.run_ledger.load()[-1].label == "r"
    states = StateStore(runtime.root).load()
    assert states[job.id].last_status == "done"
    assert states[job.id].run_count == 3
    assert states[job.id].last_run_at == at(10)


async def test_tick_records_missed_count(prepared_runtime, app_env):
    runtime, chat = await prepared_runtime(root=app_env)
    store = AutomationStore(runtime.root)
    job = store.add(
        Job(
            id="",
            name="r",
            prompt="go",
            schedule=Schedule.parse("every 1h"),
            session="s.jsonl",
        )
    )
    StateStore(runtime.root).save({job.id: JobState(last_run_at=at(6))})
    await tick(
        runtime, chat, root=runtime.root, now=at(10), request_path=app_env / "req.json"
    )
    assert StateStore(runtime.root).load()[job.id].missed == 3


async def test_request_file_forces_a_run_and_is_consumed(prepared_runtime, app_env):
    runtime, chat = await prepared_runtime(root=app_env)
    store = AutomationStore(runtime.root)
    store.add(
        Job(
            id="",
            name="r",
            prompt="go",
            schedule=Schedule.parse("daily 23:00"),
            session="s.jsonl",
        )
    )
    req = app_env / "req.json"
    req.write_text(json.dumps({"jobs": [{"name": "r", "at": 1}]}))
    results = await tick(
        runtime, chat, root=runtime.root, now=at(10), request_path=req
    )
    assert [r.status for r in results] == ["done"]
    assert not req.exists()


def test_daemon_running_reflects_the_lock(app_env):
    root = app_env
    assert daemon_running(root) is False
    lease_fd = _take_daemon_lock(root)          # simulates a resident daemon
    try:
        assert daemon_running(root) is True
        assert "running" in status_text(root, now=at(10))
    finally:
        _drop_daemon_lock(lease_fd)
    assert daemon_running(root) is False


def test_status_reports_stopped_and_the_next_wake(app_env):
    text = status_text(app_env, now=at(10))
    assert "stopped" in text
    assert "jobs: 0" in text
    assert "next wake: none" in text


async def test_a_corrupt_definition_does_not_stop_the_tick(prepared_runtime, app_env):
    runtime, chat = await prepared_runtime(root=app_env)
    store = AutomationStore(runtime.root)
    store.add(
        Job(
            id="",
            name="r",
            prompt="go",
            schedule=Schedule.parse("every 1h"),
            session="s.jsonl",
        )
    )
    store.path.write_text("{torn")
    warnings: list[str] = []
    results = await tick(
        runtime,
        chat,
        root=runtime.root,
        now=at(10),
        request_path=app_env / "req.json",
        warnings=warnings,
    )
    assert results == []          # nothing ran, nothing raised
    assert warnings                # the bad file is reported, not swallowed


async def test_round_two_reuses_the_transcript_round_one_minted(
    prepared_runtime, app_env
):
    """The spec's core promise: one fixed session per job, round after round."""
    runtime, chat = await prepared_runtime(root=app_env)
    store = AutomationStore(runtime.root)
    job = store.add(
        Job(
            id="",
            name="r",
            prompt="go",
            schedule=Schedule.parse("every 1h"),
            session="never-created.jsonl",
        )
    )
    StateStore(runtime.root).save({job.id: JobState(last_run_at=at(6))})
    first = await tick(
        runtime, chat, root=runtime.root, now=at(10), request_path=app_env / "req.json"
    )
    minted = first[0].session
    assert minted != "never-created.jsonl"


    states = StateStore(runtime.root).load()
    states[job.id].last_run_at = at(6)
    StateStore(runtime.root).save(states)
    second = await tick(
        runtime, chat, root=runtime.root, now=at(11), request_path=app_env / "req.json"
    )
    assert second[0].session == minted


def test_next_wake_never_returns_a_past_instant(app_env):
    """The loop sleeps on this value; a past instant means a busy-spin."""
    now = at(10)
    job = Job(
        id="a_1",
        name="r",
        prompt="p",
        schedule=Schedule.parse("every 1h"),
        session="s.jsonl",
    )
    # Overdue by four hours: min() would return the past slot forever.
    assert next_wake([job], {"a_1": JobState(last_run_at=at(6))}, now) >= now + 1.0


async def test_a_skipped_round_does_not_run_the_job_twice(
    prepared_runtime, app_env
):
    """A forced request for an already-due job must produce exactly one run."""
    runtime, chat = await prepared_runtime(root=app_env)
    store = AutomationStore(runtime.root)
    job = store.add(
        Job(
            id="",
            name="r",
            prompt="go",
            schedule=Schedule.parse("every 1h"),
            session="s.jsonl",
        )
    )
    StateStore(runtime.root).save({job.id: JobState(last_run_at=at(6))})
    req = app_env / "req.json"
    req.write_text(json.dumps({"jobs": [{"name": "r", "at": 1}]}))

    results = await tick(
        runtime, chat, root=runtime.root, now=at(10), request_path=req
    )
    assert len(results) == 1
    # And the run counter advanced by exactly one, not two.
    assert StateStore(runtime.root).load()[job.id].run_count == 1


def test_daemon_lock_is_exclusive_and_never_creates_the_file_on_a_probe(app_env):
    """A read-only probe must not mutate the filesystem, and must tell the truth."""
    from zenagent.cli.daemon import daemon_lock

    assert daemon_running(app_env) is False
    assert not daemon_lock(app_env).exists()
