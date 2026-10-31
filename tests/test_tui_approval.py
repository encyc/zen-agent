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


async def test_the_dialog_is_a_framed_panel_with_visible_shortcuts(picker_app):
    from textual.widgets import Static

    from zenagent.cli.tui.screens.modals import DialogChoice

    app = picker_app
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        task = await _open_approval(app)

        panel = app.screen.query_one("#dialog")
        title = app.screen.query_one("#dialog-title", Static)
        assert panel.region.width >= 40, "the dialog must be a real panel, not bare lines"
        assert "allow write_file?" in title.content.plain

        choices = list(app.screen.query(DialogChoice))
        assert [choice.plain for choice in choices] == [
            "[y] allow",
            "[a] allow for this session",
            "[n] deny",
        ]
        hint = app.screen.query_one("#dialog-hint", Static)
        assert "120s" in hint.content.plain

        await pilot.press("n")
        assert await task == "deny"


async def test_clicking_a_choice_approves(picker_app):
    from zenagent.cli.tui.screens.modals import DialogChoice

    app = picker_app
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        task = await _open_approval(app)

        allow = next(iter(app.screen.query(DialogChoice)))
        await pilot.click(allow)
        assert await task == "allow"


async def test_enter_does_not_approve_by_accident(picker_app):
    app = picker_app
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        task = await _open_approval(app)

        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, ToolApproval), "a stray Enter must not approve"

        await pilot.press("n")
        assert await task == "deny"