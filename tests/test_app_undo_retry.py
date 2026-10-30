"""undo / retry: session truncation that stays recoverable."""

from zenagent.app.chat import ChatController
from zenagent.app.commands import AppServices, build_default_commands
from zenagent.app.models import ModelManager
from zenagent.app.sessions import SessionManager


def services(runtime) -> AppServices:
    return AppServices(
        runtime=runtime,
        chat=ChatController(runtime),
        models=ModelManager(runtime),
        sessions=SessionManager(runtime),
    )


async def run_turn(chat, text: str) -> None:
    await chat.submit(text)
    await chat.wait_for_idle()


async def test_undo_drops_the_last_user_turn_and_writes_a_new_session(runtime_factory):
    from conftest import sequenced_stream

    runtime = await runtime_factory(stream_fn=sequenced_stream(["first answer", "second answer"]))
    svc = services(runtime)
    await run_turn(svc.chat, "one")
    await run_turn(svc.chat, "two")
    before = len(runtime.agent.messages)
    old_session = svc.sessions.current()

    result = svc.sessions.undo()
    assert result is not None
    dropped, path = result
    assert dropped == 2, "the user message and the answer"
    assert len(runtime.agent.messages) == before - 2
    assert path.exists() and path != old_session
    assert old_session.exists(), "an undo never destroys the old transcript"
    last = runtime.agent.messages[-1]
    text = last.content if isinstance(last.content, str) else last.content[0].text
    assert text == "first answer"


async def test_undo_with_nothing_to_drop_is_a_message(runtime_factory):
    runtime = await runtime_factory()
    svc = services(runtime)
    commands = build_default_commands(svc)
    result = await commands.dispatch("undo", "")
    assert result.message == "nothing to undo"
    assert result.reset_transcript is False


async def test_undo_command_clears_the_transcript(runtime_factory):
    from conftest import sequenced_stream

    runtime = await runtime_factory(stream_fn=sequenced_stream(["a"]))
    svc = services(runtime)
    commands = build_default_commands(svc)
    await run_turn(svc.chat, "hello")
    assert svc.chat.transcript

    result = await commands.dispatch("undo", "")
    assert result.reset_transcript is True
    assert "undid the last turn" in result.message
    assert svc.chat.transcript == []


async def test_retry_reruns_the_last_prompt(runtime_factory):
    from conftest import sequenced_stream

    runtime = await runtime_factory(stream_fn=sequenced_stream(["first", "second"]))
    serc = services(runtime)
    chat = serc.chat
    await run_turn(chat, "do the thing")

    text = chat.retry()
    assert text == "do the thing"
    await chat.wait_for_idle()

    # The prompt is neither duplicated nor lost.
    prompts = [m.content for m in runtime.agent.messages if type(m).__name__ == "UserMessage"]
    assert prompts == ["do the thing"]
    assert chat.transcript  # the new answer is rendered


async def test_retry_without_an_answer_is_refused(runtime_factory):
    runtime = await runtime_factory()
    svc = services(runtime)
    commands = build_default_commands(svc)
    result = await commands.dispatch("retry", "")
    assert result.message == "nothing to retry"


async def test_undo_and_retry_are_tui_commands(runtime_factory):
    """Both must be reachable from the hint list, not just the registry."""
    from zenagent.cli.tui.app import ZenAgentApp

    runtime = await runtime_factory()
    app = ZenAgentApp(runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        prompt = app.query_one("PromptInput")
        prompt.focus()
        for char in "/und":
            await pilot.press(char)
        await pilot.pause()
        from zenagent.cli.tui.widgets.command_hints import CommandHints

        assert "undo" in [name for name, _ in app.query_one(CommandHints).matches]

        prompt.clear()
        for char in "/ret":
            await pilot.press(char)
        await pilot.pause()
        assert "retry" in [name for name, _ in app.query_one(CommandHints).matches]
