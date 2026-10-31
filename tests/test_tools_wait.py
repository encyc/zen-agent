"""wait tool tests: wait for background bash jobs and report their results.

The bash tool is the producer (``async: true``); the wait tool consumes. Both
share the process-wide ``JobManager`` singleton, so every test closes it on
teardown to leave the next test a clean slate.
"""

import time

import pytest

from zenagent.tools import ALL_TOOLS
from zenagent.tools.bash import BASH
from zenagent.tools.jobs import get_job_manager
from zenagent.tools.wait import WAIT


@pytest.fixture(autouse=True)
async def clean_job_manager():
    yield
    await get_job_manager().close()


async def start_job(command: str, **args) -> str:
    """Start a background bash job and return its id."""
    result = await BASH.execute(
        "call_start", {"command": command, "async": True, **args}, None
    )
    assert not result.is_error, result.content[0].text
    return result.details["job_id"]


# ---------------------------------------------------------------------------
# schema / registration
# ---------------------------------------------------------------------------


def test_wait_is_a_read_tier_sequential_tool():
    assert WAIT.name == "wait"
    assert WAIT.approval == "read"
    assert WAIT.execution_mode == "sequential"
    assert WAIT.parameters["required"] == []


def test_wait_schema_declares_optional_job_id_and_timeout():
    props = WAIT.parameters["properties"]
    assert props["job_id"]["type"] == "string"
    assert props["timeout_s"]["type"] == "number"
    assert props["timeout_s"].get("default") == 120


def test_all_tools_lists_wait_right_after_bash():
    names = [tool.name for tool in ALL_TOOLS]
    assert "wait" in names
    assert names.index("wait") == names.index("bash") + 1


# ---------------------------------------------------------------------------
# waiting on a job
# ---------------------------------------------------------------------------


async def test_wait_for_a_named_job_returns_status_exit_code_and_output():
    job_id = await start_job("echo background-done")
    result = await WAIT.execute("call_wait", {"job_id": job_id, "timeout_s": 5}, None)

    assert not result.is_error
    text = result.content[0].text
    assert "background-done" in text
    assert "ok" in text and "exit code: 0" in text
    assert result.details["job_id"] == job_id
    assert result.details["status"] == "ok"
    assert result.details["exit_code"] == 0


async def test_wait_for_a_failed_job_reports_the_nonzero_exit_code():
    job_id = await start_job("exit 7")
    result = await WAIT.execute("call_wait", {"job_id": job_id, "timeout_s": 5}, None)

    assert not result.is_error  # a failed command is information, not a tool error
    assert result.details["status"] == "failed"
    assert result.details["exit_code"] == 7


async def test_wait_for_a_timed_out_job_reports_it_and_keeps_the_output():
    job_id = await start_job("echo before-sleep; sleep 10", timeout_s=1)
    result = await WAIT.execute("call_wait", {"job_id": job_id, "timeout_s": 5}, None)

    assert not result.is_error
    assert result.details["status"] == "timed_out"
    assert result.details["timed_out"] is True
    text = result.content[0].text
    assert "timed out" in text
    assert "before-sleep" in text


async def test_wait_without_a_job_id_waits_for_any_running_job():
    job_id = await start_job("echo any-job")
    result = await WAIT.execute("call_wait", {"timeout_s": 5}, None)

    assert not result.is_error
    assert result.details["job_id"] == job_id
    assert result.details["status"] == "ok"


async def test_wait_timeout_returns_a_still_running_snapshot():
    job_id = await start_job("echo before-sleep; sleep 10", timeout_s=30)
    started = time.monotonic()
    # timeout_s is clamped to its 1s floor, so this waits about one second.
    result = await WAIT.execute(
        "call_wait", {"job_id": job_id, "timeout_s": 0.01}, None
    )
    elapsed = time.monotonic() - started

    assert elapsed >= 0.9, f"wait timeout was not clamped ({elapsed:.2f}s)"
    assert elapsed < 5.0, f"wait timeout did not return promptly ({elapsed:.1f}s)"
    assert not result.is_error
    text = result.content[0].text
    assert "still running" in text
    assert "before-sleep" in text
    assert result.details["job_id"] == job_id
    assert result.details["status"] == "running"
    assert result.details["exit_code"] is None


# ---------------------------------------------------------------------------
# empty / unknown jobs
# ---------------------------------------------------------------------------


async def test_wait_with_no_jobs_says_nothing_to_wait_for():
    await get_job_manager().close()
    result = await WAIT.execute("call_wait", {}, None)

    assert not result.is_error
    assert "nothing to wait for" in result.content[0].text


async def test_wait_unknown_job_is_an_error_naming_the_known_ids():
    job_id = await start_job("true")
    result = await WAIT.execute("call_wait", {"job_id": "bg_999"}, None)

    assert result.is_error
    text = result.content[0].text
    assert "bg_999" in text
    assert job_id in text


async def test_wait_unknown_job_with_no_known_jobs_says_none_are_known():
    await get_job_manager().close()
    result = await WAIT.execute("call_wait", {"job_id": "bg_42"}, None)

    assert result.is_error
    text = result.content[0].text
    assert "bg_42" in text
    assert "none" in text.lower() or "no job" in text.lower()


async def test_wait_rejects_a_non_numeric_timeout():
    result = await WAIT.execute("call_wait", {"timeout_s": "soon"}, None)

    assert result.is_error
    assert "timeout_s" in result.content[0].text


async def test_abort_returns_promptly_and_leaves_the_job_running():
    import asyncio

    from zenagent.events import AbortToken

    job_id = await start_job("sleep 10", timeout_s=30)
    token = AbortToken()

    async def abort_soon():
        await asyncio.sleep(0.1)
        token.abort()

    task = asyncio.create_task(abort_soon())
    started = time.monotonic()
    result = await WAIT.execute(
        "call_wait", {"job_id": job_id, "timeout_s": 2}, token
    )
    elapsed = time.monotonic() - started
    await task

    assert elapsed < 1.4, f"abort did not interrupt wait ({elapsed:.1f}s)"
    assert not result.is_error
    assert "aborted" in result.content[0].text.lower()
    # Aborting a wait must not kill the background job.
    assert get_job_manager().snapshot(job_id).status == "running"

