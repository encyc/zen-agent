"""JobManager tests: background jobs, statuses, timeouts, wait and cleanup.

Commands are POSIX shell one-liners (macOS/Linux), like ``test_tools_bash.py``;
nothing touches the network. Timeouts used here are deliberately short — the
job manager is the primitive and takes the raw seconds, while the bash tool
clamps the model-facing value.
"""

import asyncio
import time

import pytest

from zenagent.tools.jobs import (
    DEFAULT_JOB_TIMEOUT_S,
    JobManager,
    JobStartError,
    UnknownJobError,
)
from zenagent.tools.shell import MAX_OUTPUT_BYTES, ShellError, kill_process_group


@pytest.fixture
async def manager():
    jobs = JobManager()
    try:
        yield jobs
    finally:
        await jobs.close()


async def test_start_returns_a_running_job_immediately(manager):
    started = time.monotonic()
    job = await manager.start("sleep 0.5; echo done", timeout_s=5)
    elapsed = time.monotonic() - started

    assert elapsed < 0.5, f"start blocked for {elapsed:.2f}s"
    assert job.status == "running"
    assert job.id.startswith("bg_")
    assert job.command == "sleep 0.5; echo done"
    assert job.exit_code is None
    assert job.timeout_s == 5
    assert job.started_at > 0 and job.finished_at is None


async def test_job_completes_ok_with_output_and_timestamps(manager):
    job = await manager.start("echo hello", timeout_s=5)
    done = await manager.wait(job.id, timeout_s=5)

    assert done is job
    assert done.status == "ok"
    assert done.exit_code == 0
    assert "hello" in done.output
    assert done.timed_out is False
    assert done.finished_at is not None and done.finished_at >= done.started_at
    assert done.duration_ms is not None and done.duration_ms >= 0


async def test_failed_command_reports_failed_status_and_exit_code(manager):
    job = await manager.start("exit 3", timeout_s=5)
    done = await manager.wait(job.id, timeout_s=5)

    assert done.status == "failed"
    assert done.exit_code == 3
    assert done.timed_out is False


async def test_timeout_kills_the_job_and_keeps_its_partial_output(manager):
    started = time.monotonic()
    job = await manager.start("echo before; sleep 10", timeout_s=0.2)
    done = await manager.wait(job.id, timeout_s=5)
    elapsed = time.monotonic() - started

    assert elapsed < 5.0, f"timeout did not return promptly ({elapsed:.1f}s)"
    assert done.status == "timed_out"
    assert done.timed_out is True
    assert "before" in done.output
    # The process was actually killed and reaped.
    assert done._proc is not None and done._proc.returncode is not None


async def test_wait_without_a_job_id_waits_for_the_running_job(manager):
    job = await manager.start("echo the-only-job", timeout_s=5)
    done = await manager.wait(timeout_s=5)

    assert done is job
    assert done.status == "ok"
    assert "the-only-job" in done.output


async def test_wait_returns_a_finished_job_when_nothing_is_running(manager):
    job = await manager.start("true", timeout_s=5)
    # Let the monitor finish without consuming the job through wait().
    await asyncio.wait_for(job._done.wait(), timeout=5)
    finished = await manager.wait(timeout_s=5)

    assert finished is job
    assert finished.status == "ok"


async def test_wait_with_no_jobs_returns_none(manager):
    assert await manager.wait(timeout_s=1) is None


async def test_wait_unknown_job_raises_with_the_known_ids(manager):
    job = await manager.start("true", timeout_s=5)
    with pytest.raises(UnknownJobError) as info:
        await manager.wait("bg_999", timeout_s=1)

    assert info.value.job_id == "bg_999"
    assert job.id in info.value.known


async def test_wait_timeout_returns_a_running_snapshot(manager):
    job = await manager.start("echo started; sleep 10", timeout_s=30)
    started = time.monotonic()
    snapshot = await manager.wait(job.id, timeout_s=0.2)
    elapsed = time.monotonic() - started

    assert elapsed < 5.0
    assert snapshot is job
    assert snapshot.status == "running"
    assert snapshot.exit_code is None
    assert "started" in snapshot.output
    # A timed-out wait must not kill the job: it is still running.
    assert job.status == "running"


async def test_cleanup_on_wait_removes_the_finished_job(manager):
    job = await manager.start("true", timeout_s=5)
    await manager.wait(job.id, timeout_s=5)

    assert manager.snapshot(job.id) is None
    assert manager.list() == []


async def test_snapshot_and_list_expose_jobs_in_start_order(manager):
    first = await manager.start("true", timeout_s=5)
    second = await manager.start("true", timeout_s=5)

    assert [job.id for job in manager.list()] == [first.id, second.id]
    assert manager.snapshot(first.id) is first
    assert manager.snapshot("bg_does_not_exist") is None


async def test_job_ids_are_unique(manager):
    first = await manager.start("true", timeout_s=5)
    second = await manager.start("true", timeout_s=5)

    assert first.id != second.id
    assert first.id == "bg_1"
    assert second.id == "bg_2"


async def test_output_is_capped_head_and_tail(manager):
    job = await manager.start("yes x | head -c 40000", timeout_s=20)
    done = await manager.wait(job.id, timeout_s=20)

    assert done.truncated is True
    assert "truncated" in done.output and "bytes of output" in done.output
    assert len(done.output) < MAX_OUTPUT_BYTES + 2000


async def test_invalid_cwd_raises_a_job_start_error(manager):
    with pytest.raises(JobStartError):
        await manager.start("true", cwd="does/not/exist", timeout_s=5)


async def test_unknown_shell_raises_a_shell_error(manager, monkeypatch):
    monkeypatch.setenv("ZENAGENT_SHELL", "fish")
    with pytest.raises(ShellError):
        await manager.start("true", timeout_s=5)


async def test_close_kills_running_jobs_and_drops_them():
    jobs = JobManager()
    job = await jobs.start("sleep 10", timeout_s=30)
    proc = job._proc

    await jobs.close()

    assert jobs.list() == []
    assert proc is not None and proc.returncode is not None


async def test_default_job_timeout_is_ten_minutes(manager):
    assert DEFAULT_JOB_TIMEOUT_S == 600
    job = await manager.start("true")
    assert job.timeout_s == DEFAULT_JOB_TIMEOUT_S


async def test_explicit_kill_removes_the_process_group(manager):
    job = await manager.start("echo alive; sleep 10", timeout_s=30)
    proc = job._proc
    assert proc is not None
    await kill_process_group(proc)

    await manager.wait(job.id, timeout_s=5)
    assert proc.returncode is not None


async def test_wait_leaves_no_extra_tasks_behind(manager):
    before = asyncio.all_tasks()
    job = await manager.start("echo done", timeout_s=5)
    await manager.wait(job.id, timeout_s=5)

    leftover = {
        task
        for task in asyncio.all_tasks() - before
        if not task.done() and task is not asyncio.current_task()
    }
    assert leftover == set(), leftover
