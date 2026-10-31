"""Ask-mode approval: the modal's choices and the app-level timeout."""

import asyncio

from zenagent.cli.tui.screens.modals import ToolApproval


async def _open_approval(app):
    """Start a request and wait until its modal is on top."""
    task = asyncio.create_task(
        app.request_tool_approval("write_file", {"path": "x.txt"})
    )
    for _ in range(100):
        if isinstance(app.screen, ToolApproval):
            return task
        await asyncio.sleep(0.01)
    raise AssertionError("approval modal never appeared")


async def test_approval_allow_returns_allow(picker_app):
    app = picker_app
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        task = await _open_approval(app)
        await pilot.press("y")
        assert await task == "allow"


async def test_approval_always_returns_always(picker_app):
    app = picker_app
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        task = await _open_approval(app)
        await pilot.press("a")
        assert await task == "always"


async def test_approval_deny_returns_deny(picker_app):
    app = picker_app
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        task = await _open_approval(app)
        await pilot.press("n")
        assert await task == "deny"


async def test_approval_timeout_denies_and_closes(picker_app):
    app = picker_app
    app.approval_timeout_s = 0.05
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        task = await _open_approval(app)
        assert await asyncio.wait_for(task, timeout=2) == "deny"
        assert not isinstance(app.screen, ToolApproval)


async def test_approval_modal_shows_the_grant_target(picker_app):
    app = picker_app
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        task = await _open_approval(app)
        assert getattr(app.screen, "target", None)
        assert "write_file" in app.screen.target
        await pilot.press("n")
        await task
