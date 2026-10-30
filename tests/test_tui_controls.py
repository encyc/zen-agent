"""Run controls: follow-up queue, interrupt and queue clearing."""

from zenagent.cli.tui.widgets.status_band import StatusBand


async def test_queue_is_visible_and_ctrl_c_aborts(gated_app):
    app, gate = gated_app
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await app.chat.submit("hi")
        await pilot.pause()
        assert app.chat.status.running is True

        app.chat.queue_follow_up("queued message")
        await pilot.pause()
        assert "1 queued" in app.query_one(StatusBand).plain

        await pilot.press("ctrl+c")
        gate.set()
        await pilot.pause()
        await app.chat.wait_for_idle()
        await pilot.pause()
        assert app.query_one(StatusBand) is not None  # app survived
        assert app.chat.status.running is False


from zenagent.cli.tui.app import ZenAgentApp
from zenagent.cli.tui.widgets.prompt_input import PromptInput


async def test_alt_up_puts_the_last_queued_message_back_in_the_prompt(gated_app):
    app, gate = gated_app
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await app.chat.submit("first")
        await pilot.pause()

        prompt = app.query_one(PromptInput)
        prompt.insert("queued text")
        await pilot.press("ctrl+q")
        await pilot.pause()
        assert app.chat.status.follow_ups == 1

        await pilot.press("alt+up")
        await pilot.pause()
        assert prompt.text == "queued text"
        assert app.chat.status.follow_ups == 0

        gate.set()
        await app.chat.wait_for_idle()


async def test_double_escape_with_an_empty_prompt_undoes_the_last_turn(fake_runtime):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await app.chat.submit("hi")
        await app.chat.wait_for_idle()
        await pilot.pause()
        assert fake_runtime.agent.messages

        await pilot.press("escape")
        await pilot.press("escape")
        await pilot.pause()
        await pilot.pause()
        assert fake_runtime.agent.messages == []


async def test_single_escape_does_not_undo(fake_runtime):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await app.chat.submit("hi")
        await app.chat.wait_for_idle()
        await pilot.pause()

        await pilot.press("escape")
        await pilot.pause()
        assert fake_runtime.agent.messages


async def test_escape_while_running_only_aborts(gated_app):
    app, gate = gated_app
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await app.chat.submit("first")
        await pilot.pause()
        await pilot.press("escape")
        gate.set()
        await pilot.pause()
        await app.chat.wait_for_idle()
        # abort must not be mistaken for the double-escape rewind
        assert app.runtime.agent.messages, "a running Esc must never undo the turn"
