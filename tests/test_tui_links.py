"""End to end: the link commands survive the TUI and reach the clipboard."""

from conftest import sequenced_stream

from zenagent.cli.tui.app import ZenAgentApp

ANSWER = (
    "回调监听已起在 127.0.0.1:8971：\n\n```\n"
    "https://metabase.example.cn/oauth/authorize?response_type=code"
    "&redirect_uri=http%3A%2F%2F127.0.0.1%3A8971%2Fcallback\n```\n"
)


async def test_urls_then_copy_round_trips_the_clipped_url(runtime_factory, monkeypatch):
    copied: list[str] = []
    monkeypatch.setattr(
        "zenagent.cli.tui.clipboard.write_clipboard_text",
        lambda text: copied.append(text) or True,
    )
    runtime = await runtime_factory(stream_fn=sequenced_stream([ANSWER]))
    app = ZenAgentApp(runtime)
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        await app.chat.submit("hi")
        await app.chat.wait_for_idle()
        await pilot.pause()

        listing = await app.commands.dispatch("urls", "")
        assert "https://metabase.example.cn/oauth/authorize" in listing.message

        result = await app.commands.dispatch("copy", "1")
        assert result.message is not None
        assert copied and "%3A%2F%2F" in copied[0]
        assert app.is_running
