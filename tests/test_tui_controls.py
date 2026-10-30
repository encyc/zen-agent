"""Run controls: follow-up queue, interrupt and queue clearing."""

from zenagent.cli.tui.widgets.status_bar import StatusBar


async def test_queue_is_visible_and_ctrl_c_aborts(gated_app):
    app, gate = gated_app
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await app.chat.submit("hi")
        await pilot.pause()
        assert app.chat.status.running is True

        app.chat.queue_follow_up("queued message")
        await pilot.pause()
        assert "queued 1" in app.query_one(StatusBar).plain

        await pilot.press("ctrl+c")
        gate.set()
        await pilot.pause()
        await app.chat.wait_for_idle()
        await pilot.pause()
        assert app.query_one(StatusBar) is not None  # app survived
        assert app.chat.status.running is False
