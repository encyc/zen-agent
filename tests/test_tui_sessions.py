"""TUI session management: picker actions, delete confirmation, rename prompt."""

import asyncio
from pathlib import Path

from textual.widgets import Input

from zenagent.agent.session import session_title
from zenagent.cli.tui.screens.modals import Confirm, RenamePrompt, SessionPicker


async def _wait_for_screen(app, screen_type):
    """Poll until ``app.screen`` is the requested modal (workers are async)."""
    for _ in range(300):
        if isinstance(app.screen, screen_type):
            return app.screen
        await asyncio.sleep(0.01)
    raise AssertionError(
        f"{screen_type.__name__} never appeared (screen={app.screen!r})"
    )


async def _wait_until(predicate):
    for _ in range(300):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition never became true")


async def test_session_picker_new_switches_to_a_fresh_session(picker_app):
    app = picker_app
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        before = app.runtime.session.path
        app.open_session_picker()
        await _wait_for_screen(app, SessionPicker)
        await pilot.press("n")
        await _wait_until(lambda: app.runtime.session.path != before)
        assert not app.chat.transcript


async def test_session_picker_fork_switches_to_a_copy(picker_app):
    app = picker_app
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        before = app.runtime.session.path
        app.open_session_picker()
        await _wait_for_screen(app, SessionPicker)
        await pilot.press("f")
        await _wait_until(lambda: app.runtime.session.path != before)
        assert before.exists(), "fork must not modify the original session"


async def test_session_picker_delete_needs_confirmation(picker_app):
    app = picker_app
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        current = app.runtime.session.path

        app.open_session_picker()
        await _wait_for_screen(app, SessionPicker)
        await pilot.press("d")
        await _wait_for_screen(app, Confirm)
        await pilot.press("n")
        await pilot.pause()
        assert current.exists(), "cancelling the confirmation must keep the session"

        app.open_session_picker()
        await _wait_for_screen(app, SessionPicker)
        await pilot.press("d")
        await _wait_for_screen(app, Confirm)
        await pilot.press("y")
        await _wait_until(lambda: not current.exists())
        assert (current.parent / ".trash" / current.name).exists()


async def test_session_picker_rename_updates_the_display_name(picker_app):
    app = picker_app
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.open_session_picker()
        await _wait_for_screen(app, SessionPicker)
        await pilot.press("r")
        await _wait_for_screen(app, RenamePrompt)
        app.screen.query_one("#rename-input", Input).value = "release checklist"
        await pilot.press("enter")
        await _wait_until(
            lambda: session_title(app.runtime.session.path) == "release checklist"
        )

        entries = app.sessions.entries()
        assert any(entry.title == "release checklist" for entry in entries)
        # The picker derives its labels from those titles; inspect the model
        # rather than re-opening a modal mid-transition.
        picker = SessionPicker(entries)
        assert any("release checklist" in item.label for item in picker.items)


async def test_session_picker_tab_toggles_the_scope(fake_runtime):
    from zenagent.cli.tui.app import ZenAgentApp
    from zenagent.cli.tui.screens.modals import SessionPicker

    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.open_session_picker("project")
        await _wait_for_screen(app, SessionPicker)
        await pilot.press("tab")
        for _ in range(100):
            if isinstance(app.screen, SessionPicker) and app.screen.scope == "all":
                break
            await asyncio.sleep(0.01)
        assert app.screen.scope == "all"


def test_session_picker_all_scope_labels_the_project():
    from zenagent.app.sessions import SessionEntry
    from zenagent.cli.tui.screens.modals import SessionPicker

    entries = [
        SessionEntry(
            path=Path("/tmp/sessions/20260101-000000.jsonl"),
            created=1.0,
            messages=2,
            size=10,
            current=False,
            title="fix bug",
            project="other-app",
        )
    ]
    picker = SessionPicker(entries, scope="all")
    assert any("[other-app]" in item.label for item in picker.items)
