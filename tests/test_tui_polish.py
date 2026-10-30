"""Polish: exit summary and narrow status bar."""

from zenagent.cli.tui.widgets.status_band import StatusBand


async def test_quit_records_session_summary(polish_app):
    app = polish_app
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+q")
        await pilot.pause()
    assert app.exit_summary is not None
    assert "session:" in app.exit_summary and "$" in app.exit_summary


async def test_narrow_band_drops_facts_before_identity(polish_app):
    """omp drops the right group (facts) before the left group (who/where)."""
    app = polish_app
    async with app.run_test(size=(40, 24)) as pilot:
        await pilot.pause()
        text = app.query_one(StatusBand).plain
        assert "$" not in text and "context" not in text, "facts go first"
        assert app.runtime.agent.model.id in text, "identity outlives the facts"
