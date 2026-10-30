"""Polish: exit summary and narrow status bar."""

from zenagent.cli.tui.widgets.status_bar import StatusBar


async def test_quit_records_session_summary(polish_app):
    app = polish_app
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+q")
        await pilot.pause()
    assert app.exit_summary is not None
    assert "session:" in app.exit_summary and "$" in app.exit_summary


async def test_narrow_status_bar_drops_thinking(polish_app):
    app = polish_app
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        text = app.query_one(StatusBar).plain
        assert "thinking" not in text
