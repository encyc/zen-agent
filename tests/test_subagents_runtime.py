"""TaskManager: the process-level registry for background child runs.

Moved verbatim from ``tests/test_task.py`` (2026-11-07 cleanup Task 12) so the
registry's contract is tested next to its new home, ``zenagent.agent.subagents_runtime``.
"""

import asyncio

# --- TaskManager: process-level registry for background children -------------


def test_task_manager_start_and_wait_roundtrip():
    async def main():
        from zenagent.agent.subagents_runtime import get_task_manager

        manager = get_task_manager()

        def factory():
            async def run():
                return "envelope-text", {"status": "ok"}

            return run()

        run = manager.start("call-1", "Research", "child-model", factory)
        assert run.task_id.startswith("task_")
        assert run.status == "running"
        assert (run.call_id, run.name, run.model_id) == ("call-1", "Research", "child-model")

        done = await manager.wait(run.task_id, timeout_s=5)
        assert done is run
        assert done.status == "ok"
        assert done.envelope == "envelope-text"
        assert done.details == {"status": "ok"}

    asyncio.run(main())


def test_task_manager_wait_is_idempotent_after_completion():
    async def main():
        from zenagent.agent.subagents_runtime import get_task_manager

        manager = get_task_manager()

        def factory():
            async def run():
                return "e", {}

            return run()

        run = manager.start("call-1", "T", "m", factory)
        first = await manager.wait(run.task_id, timeout_s=5)
        second = await manager.wait(run.task_id, timeout_s=5)
        assert first is second and first.envelope == "e"

    asyncio.run(main())


def test_task_manager_unknown_id_raises_with_known_list():
    async def main():
        from zenagent.agent.subagents_runtime import UnknownTaskError, get_task_manager

        manager = get_task_manager()

        def factory():
            async def run():
                return "e", {}

            return run()

        run = manager.start("call-1", "T", "m", factory)
        await manager.wait(run.task_id, timeout_s=5)
        try:
            await manager.wait("task_nope", timeout_s=1)
        except UnknownTaskError as exc:
            assert exc.task_id == "task_nope"
            assert run.task_id in exc.known
        else:
            raise AssertionError("expected UnknownTaskError")

    asyncio.run(main())


def test_task_manager_running_call_ids_tracks_lifecycle():
    async def main():
        import asyncio as aio

        from zenagent.agent.subagents_runtime import get_task_manager

        manager = get_task_manager()
        gate = aio.Event()

        def factory():
            async def run():
                await gate.wait()
                return "e", {}

            return run()

        run = manager.start("call-1", "T", "m", factory)
        assert manager.running_call_ids() == {"call-1"}
        gate.set()
        await manager.wait(run.task_id, timeout_s=5)
        assert manager.running_call_ids() == set()

    asyncio.run(main())


def test_task_manager_retention_keeps_last_32():
    async def main():
        from zenagent.agent.subagents_runtime import (
            TASK_RUN_RETENTION,
            UnknownTaskError,
            get_task_manager,
        )

        manager = get_task_manager()

        def factory():
            async def run():
                return "e", {}

            return run()

        first = manager.start("call-0", "T", "m", factory)
        await manager.wait(first.task_id, timeout_s=5)
        for i in range(1, TASK_RUN_RETENTION + 1):
            run = manager.start(f"call-{i}", "T", "m", factory)
            await manager.wait(run.task_id, timeout_s=5)

        assert len(manager.ids()) == TASK_RUN_RETENTION
        try:
            await manager.wait(first.task_id, timeout_s=1)
        except UnknownTaskError:
            pass
        else:
            raise AssertionError("oldest completed run should have been pruned")

    asyncio.run(main())


def test_task_manager_error_run_carries_status_details():
    async def main():
        from zenagent.agent.subagents_runtime import get_task_manager

        manager = get_task_manager()

        def factory():
            async def run():
                raise RuntimeError("boom")

            return run()

        run = manager.start("call-1", "T", "m", factory)
        done = await manager.wait(run.task_id, timeout_s=5)
        assert done.status == "error"
        assert done.details.get("status") == "error"
        assert "boom" in done.envelope

    asyncio.run(main())
