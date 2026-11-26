# tests/test_automation_runner.py
import time

from zenagent.app.automation_runner import run_round
from zenagent.app.automations import Job, Schedule
from zenagent.app.session_lease import acquire_session, release_session


def job(timeout_s=5, session="s.jsonl"):
    return Job(
        id="a_1",
        name="r",
        prompt="go",
        schedule=Schedule.parse("daily 09:00"),
        session=session,
        timeout_s=timeout_s,
    )


async def test_round_writes_the_session_and_the_ledger(prepared_runtime, app_env):
    runtime, chat = await prepared_runtime(root=app_env)
    result = await run_round(
        runtime,
        chat,
        job(),
        run_number=1,
        now=time.time(),
        session_root=runtime.root,
    )
    assert result.status == "done"
    rows = runtime.run_ledger.load()
    assert rows[-1].kind == "automation"
    assert rows[-1].label == "r"
    assert rows[-1].status == "done"
    assert chat.busy is False


async def test_busy_session_is_skipped_without_a_run(prepared_runtime, app_env):
    runtime, chat = await prepared_runtime(root=app_env)
    # A third path the round will switch into — not the runtime's current
    # session, which its own Runtime already holds.
    from zenagent.app.automations import resolve_session as _resolve

    target = _resolve(runtime.root, "held-by-the-user.jsonl")
    # The file must exist BEFORE the lease is taken: a missing transcript is
    # re-minted by design, so the round would never reach the busy check.
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("", encoding="utf-8")
    lease = acquire_session(target, object())     # the user has this open
    try:
        result = await run_round(
            runtime,
            chat,
            job(session=target.name),
            run_number=1,
            now=time.time(),
            session_root=runtime.root,
        )
    finally:
        release_session(lease)
    assert result.status == "skipped"
    assert result.skip_reason == "session-busy"
    assert runtime.run_ledger.load() == []


async def test_timeout_aborts_and_leaves_the_controller_idle(prepared_runtime, app_env):
    runtime, chat = await prepared_runtime(hang=True, root=app_env)
    result = await run_round(
        runtime,
        chat,
        job(timeout_s=0.05),
        run_number=1,
        now=time.time(),
        session_root=runtime.root,
    )
    assert result.status == "timeout"
    assert chat.busy is False
    rows = runtime.run_ledger.load()
    assert rows[-1].status == "failed"
    assert rows[-1].termination == "timeout"


async def test_missing_session_file_is_minted_not_fatal(prepared_runtime, app_env):
    runtime, chat = await prepared_runtime(root=app_env)
    result = await run_round(
        runtime,
        chat,
        job(session="gone.jsonl"),
        run_number=1,
        now=time.time(),
        session_root=runtime.root,
    )
    assert result.status == "done"
    assert result.session
    assert result.session != "gone.jsonl"


async def test_the_lease_is_released_after_the_round(prepared_runtime, app_env):
    runtime, chat = await prepared_runtime(root=app_env)
    await run_round(
        runtime,
        chat,
        job(),
        run_number=1,
        now=time.time(),
        session_root=runtime.root,
    )
    # A fresh owner can take the session: nothing is holding it.
    lease = acquire_session(runtime.session.path, object())
    release_session(lease)


async def test_a_failing_switch_still_releases_the_lease(prepared_runtime, app_env):
    """A switch that adopts the lease and then raises must not strand it.

    ``switch_session`` binds the target's lease before ``_recompose`` runs, so an
    exception there leaves the runtime holding a session the user cannot open.
    """
    from zenagent.app.session_lease import session_is_live

    runtime, chat = await prepared_runtime(root=app_env)
    target = runtime.session.path.parent / "switch-raises.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("", encoding="utf-8")

    original = runtime.switch_session

    async def boom(path, **kwargs):
        await original(path, **kwargs)
        raise RuntimeError("recompose blew up")

    runtime.switch_session = boom
    result = await run_round(
        runtime,
        chat,
        job(session=target.name),
        run_number=1,
        now=time.time(),
        session_root=runtime.root,
    )
    assert result.status == "error"
    assert session_is_live(target) is False


async def test_a_run_that_fails_reports_failed_not_done(prepared_runtime, app_env):
    """The agent's own termination is what state and the UI must show."""
    runtime, chat = await prepared_runtime(root=app_env, fail=True)
    result = await run_round(
        runtime,
        chat,
        job(),
        run_number=1,
        now=time.time(),
        session_root=runtime.root,
    )
    assert result.status == "failed"
    assert runtime.run_ledger.load()[-1].status == "failed"
