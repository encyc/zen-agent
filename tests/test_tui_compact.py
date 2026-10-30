"""TUI compaction: the transcript divider and the summary that replaces it."""

from zenagent.cli.tui.app import ZenAgentApp
from zenagent.cli.tui.widgets.prompt_input import PromptInput
from zenagent.cli.tui.widgets.rows import DividerRow, UserBlock


async def test_slash_compact_shows_a_divider_and_the_summary(fake_runtime):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        await app.chat.submit("hi")
        await app.chat.wait_for_idle()
        await pilot.pause()

        prompt = app.query_one(PromptInput)
        prompt.focus()
        prompt.insert("/compact")
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

        dividers = list(app.query(DividerRow))
        assert dividers and "compacted" in dividers[0].plain
        # The summary is a UserMessage, so it renders as a filled user block.
        assert any("此前会话摘要" in block.plain for block in app.query(UserBlock))


async def test_auto_compaction_rebuilds_the_transcript_view(runtime_factory, monkeypatch):
    import asyncio

    from conftest import sequenced_stream
    from textual.widgets import Markdown

    import zenagent.app.chat as chat_module

    real_compact = chat_module.compact
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_compact(agent, stream_fn=None):
        started.set()
        await release.wait()
        return await real_compact(agent, stream_fn)

    calls = {"n": 0}

    def first_turn_only(usage, window):
        calls["n"] += 1
        return calls["n"] == 1

    monkeypatch.setattr(chat_module, "compact", slow_compact)
    monkeypatch.setattr(chat_module, "should_auto_compact", first_turn_only)
    runtime = await runtime_factory(
        stream_fn=sequenced_stream(["first answer", "summary text", "second answer"])
    )
    app = ZenAgentApp(runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await app.chat.submit("first")
        await asyncio.wait_for(started.wait(), timeout=5)

        # Paint the pre-compaction transcript (the stale view the bug kept).
        app._refresh_status()
        await pilot.pause()
        assert len(app.query(Markdown)) == 1

        release.set()
        await app.chat.wait_for_idle()
        await pilot.pause()
        await pilot.pause()

        # The rebuilt view: no stale assistant bubble, one summary, one divider.
        assert len(app.query(Markdown)) == 0
        assert any("此前会话摘要" in block.plain for block in app.query(UserBlock))
        assert list(app.query(DividerRow))

        prompt = app.query_one(PromptInput)
        prompt.focus()
        prompt.insert("second")
        await pilot.press("enter")
        await app.chat.wait_for_idle()
        await pilot.pause()
        await pilot.pause()

        assert any(block.plain == "second" for block in app.query(UserBlock))
        assert len(app.query(Markdown)) == 1
