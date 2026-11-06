"""Polish: exit summary and narrow status bar."""

from zenagent.cli.tui.widgets.status_band import StatusBand


async def test_quit_without_saying_anything_reports_no_session(polish_app):
    """The file is dropped at close, so the summary must not print its path."""
    app = polish_app
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+d")
        await pilot.pause()
    assert app.exit_summary is not None
    assert "not saved" in app.exit_summary and "$" in app.exit_summary
    assert str(app.runtime.session.path) not in app.exit_summary


async def test_quit_records_session_summary(polish_app):
    from conftest import sequenced_stream

    app = polish_app
    app.runtime.stream_fn = sequenced_stream(["an answer"])
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await app.chat.submit("hi")
        await app.chat.wait_for_idle()
        await pilot.pause()
        await pilot.press("ctrl+d")
        await pilot.pause()
    assert app.exit_summary is not None
    assert "session:" in app.exit_summary and "$" in app.exit_summary
    assert str(app.runtime.session.path) in app.exit_summary


async def test_narrow_band_drops_facts_before_identity(polish_app):
    """omp drops the right group (facts) before the left group (who/where)."""
    app = polish_app
    async with app.run_test(size=(40, 24)) as pilot:
        await pilot.pause()
        text = app.query_one(StatusBand).plain
        assert "$" not in text and "context" not in text, "facts go first"
        assert app.runtime.agent.model.id in text, "identity outlives the facts"
