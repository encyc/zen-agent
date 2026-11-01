"""Ask-mode approval: the modal's choices and the app-level timeout."""

import asyncio

from zenagent.app.safety import Verdict
from zenagent.cli.tui.screens.modals import ToolApproval, preview_diff


async def _open_approval(
    app,
    *,
    laid_out: bool = True,
    verdict=None,
    tool: str = "write_file",
    arguments: dict | None = None,
):
    """Start a request and wait until the dialog is up (and laid out).

    ``app.screen`` flips to the modal before the first layout pass, so returning
    immediately can hand a test a 0x0 panel (a CI flake under load). Tests that
    only press a key can skip the layout wait; a tight timeout makes that race
    unavoidable.
    """
    task = asyncio.create_task(
        app.request_tool_approval(
            tool, arguments if arguments is not None else {"path": "x.txt"}, verdict=verdict
        )
    )
    for _ in range(200):
        screen = app.screen
        if isinstance(screen, ToolApproval):
            if not laid_out:
                return task
            panels = list(screen.query("#dialog"))
            if panels and panels[0].region.width > 0:
                return task
        await asyncio.sleep(0.01)
    raise AssertionError("approval modal never appeared (or never laid out)")


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
        task = await _open_approval(app, laid_out=False)
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


async def test_approval_dialog_shows_the_grant_target(picker_app):
    from textual.widgets import Static

    app = picker_app
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        task = await _open_approval(app)
        assert getattr(app.screen, "target", None)
        assert "write_file" in app.screen.target
        scope = app.screen.query_one("#dialog-scope", Static)
        assert "always allows:" in scope.content.plain
        await pilot.press("n")
        assert await task == "deny"


# ---------------------------------------------------------------------------
# Risk badge and diff preview
# ---------------------------------------------------------------------------


async def test_approval_shows_outside_risk_badge(picker_app):
    from textual.widgets import Static

    app = picker_app
    verdict = Verdict(
        "confirm",
        "outside workspace",
        risk="outside",
        target="/etc/hosts",
    )
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        task = await _open_approval(
            app, verdict=verdict, arguments={"path": "/etc/hosts", "content": "x"}
        )
        badge = app.screen.query_one("#dialog-risk", Static)
        assert "outside workspace" in badge.content.plain
        assert "/etc/hosts" in badge.content.plain
        await pilot.press("n")
        assert await task == "deny"


async def test_approval_shows_diff_preview(picker_app, tmp_path):
    from textual.widgets import Static

    target = tmp_path / "a.txt"
    target.write_text("one\ntwo\n")
    app = picker_app
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        task = await _open_approval(
            app,
            tool="edit_file",
            arguments={
                "path": str(target),
                "old_string": "two",
                "new_string": "three",
            },
        )
        block = app.screen.query_one("#dialog-diff")
        lines = [row.content.plain for row in block.query(Static)]
        assert any(line.startswith("+") and "three" in line for line in lines)
        assert any(line.startswith("-") and "two" in line for line in lines)
        await pilot.press("n")
        assert await task == "deny"


def test_preview_diff_write_file(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("old\n")
    lines = preview_diff("write_file", {"path": str(target), "content": "new\n"})
    assert any(line.startswith("+") and "new" in line for line in lines)
    assert any(line.startswith("-") and "old" in line for line in lines)


def test_preview_diff_edit_requires_a_match(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("one\ntwo\n")
    assert preview_diff(
        "edit_file",
        {"path": str(target), "old_string": "absent", "new_string": "x"},
    ) == []


def test_preview_diff_missing_file_is_empty(tmp_path):
    target = tmp_path / "nope.txt"
    assert preview_diff("write_file", {"path": str(target), "content": "x"}) == []


def test_preview_diff_truncates(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("\n".join(f"old {n}" for n in range(20)) + "\n")
    content = "\n".join(f"new {n}" for n in range(20)) + "\n"
    lines = preview_diff(
        "write_file", {"path": str(target), "content": content}, max_lines=3
    )
    assert len(lines) == 4
    assert lines[-1] == "…"


def test_preview_diff_ignores_other_tools():
    assert preview_diff("bash", {"command": "ls"}) == []
