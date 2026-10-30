"""Provider failures stay visible in a sticky banner instead of scrolling away."""

from zenagent.cli.tui.app import ZenAgentApp
from zenagent.cli.tui.widgets.error_banner import ErrorBanner
from zenagent.events import AssistantEvent
from zenagent.types import AssistantMessage, Usage


def failing_stream(message: str | None = "provider exploded"):
    async def stream(model, messages, tools, options):
        partial = AssistantMessage(
            content=[],
            provider="p",
            model="m",
            usage=Usage(),
            stop_reason="error",
            timestamp=0,
            error_message=message,
        )
        yield AssistantEvent(type="start", partial=partial)
        yield AssistantEvent(type="error", partial=partial, reason="error")

    return stream


async def test_a_failed_run_shows_a_sticky_banner(runtime_factory):
    runtime = await runtime_factory(stream_fn=failing_stream())
    app = ZenAgentApp(runtime)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        await app.chat.submit("hi")
        await app.chat.wait_for_idle()
        await pilot.pause()
        await pilot.pause()

        banner = app.query_one(ErrorBanner)
        assert banner.display is True
        assert "provider exploded" in banner.plain


async def test_clicking_the_banner_dismisses_it(runtime_factory):
    runtime = await runtime_factory(stream_fn=failing_stream())
    app = ZenAgentApp(runtime)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        await app.chat.submit("hi")
        await app.chat.wait_for_idle()
        await pilot.pause()

        await pilot.click(app.query_one(ErrorBanner))
        await pilot.pause()
        assert app.query_one(ErrorBanner).display is False
        assert app.chat.status.last_error == ""


async def test_error_without_a_message_uses_a_fallback(runtime_factory):
    runtime = await runtime_factory(stream_fn=failing_stream(message=None))
    app = ZenAgentApp(runtime)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        await app.chat.submit("hi")
        await app.chat.wait_for_idle()
        await pilot.pause()

        assert "the run failed" in app.query_one(ErrorBanner).plain
