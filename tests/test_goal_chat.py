import asyncio

from zenagent.app.chat import ChatController
from zenagent.app.goal import GoalController, GoalLimits, GoalVerdict


class FakeVerifier:
    def __init__(self, *verdicts):
        self.verdicts = list(verdicts)

    async def verify(self, goal, ctx):
        return (
            self.verdicts.pop(0)
            if self.verdicts
            else GoalVerdict("not_met", "again")
        )


class GatedVerifier:
    def __init__(self, verdict=None):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.verdict = verdict or GoalVerdict("not_met", "more")

    async def verify(self, goal, ctx):
        self.started.set()
        await self.release.wait()
        return self.verdict


def _bind_goal(runtime, verifier):
    runtime.goal = GoalController(limits=GoalLimits(), verifier=verifier)
    runtime.goal.bind_session(runtime.session)
    return runtime.goal


async def test_goal_continues_until_met(runtime_factory):
    calls = []

    async def stream(model, messages, tools, options):
        from zenagent.events import AssistantEvent
        from zenagent.types import AssistantMessage, TextContent, Usage

        calls.append(messages[-1].content)
        partial = AssistantMessage(
            content=[TextContent(text="working")],
            provider="p",
            model="m",
            usage=Usage(),
            stop_reason="stop",
            timestamp=0,
        )
        yield AssistantEvent(type="start", partial=partial)
        yield AssistantEvent(type="done", partial=partial, reason="stop")

    runtime = await runtime_factory(stream_fn=stream)
    _bind_goal(
        runtime,
        FakeVerifier(GoalVerdict("not_met", "step 1"), GoalVerdict("met", "done")),
    )
    chat = ChatController(runtime)
    chat.start_goal("fix tests")
    await chat.wait_for_idle()

    assert len(calls) == 2
    assert runtime.agent.messages[0].goal is True
    assert runtime.goal.snapshot().status == "complete"


async def test_user_message_wins_over_goal_continuation(runtime_factory):
    gate = asyncio.Event()
    calls = []

    async def stream(model, messages, tools, options):
        from zenagent.events import AssistantEvent
        from zenagent.types import AssistantMessage, TextContent, Usage

        calls.append(messages[-1].content)
        partial = AssistantMessage(
            content=[TextContent(text="working")],
            provider="p",
            model="m",
            usage=Usage(),
            stop_reason="stop",
            timestamp=0,
        )
        yield AssistantEvent(type="start", partial=partial)
        if len(calls) == 1:
            await gate.wait()
        yield AssistantEvent(type="done", partial=partial, reason="stop")

    runtime = await runtime_factory(stream_fn=stream)
    _bind_goal(
        runtime, FakeVerifier(GoalVerdict("not_met", "more"))
    )
    chat = ChatController(runtime)
    chat.start_goal("fix tests")
    await asyncio.sleep(0.02)
    await chat.submit("user says hello")
    gate.set()
    await chat.wait_for_idle()

    assert "user says hello" in calls[1]
    assert "fix tests" in calls[2]


async def test_usage_never_pauses_a_goal(runtime_factory):
    async def stream(model, messages, tools, options):
        from zenagent.events import AssistantEvent
        from zenagent.types import AssistantMessage, TextContent, Usage

        partial = AssistantMessage(
            content=[TextContent(text="burn")],
            provider="p",
            model="m",
            usage=Usage(input=100, output=100),
            stop_reason="stop",
            timestamp=0,
        )
        yield AssistantEvent(type="start", partial=partial)
        yield AssistantEvent(type="done", partial=partial, reason="stop")

    runtime = await runtime_factory(stream_fn=stream)
    _bind_goal(
        runtime, FakeVerifier(GoalVerdict("not_met", "more"))
    )
    chat = ChatController(runtime)
    chat.start_goal("fix tests")
    await chat.wait_for_idle()

    # 200 tokens of usage is reported, never enforced: the goal keeps going
    # until the verifier says otherwise.
    assert runtime.goal.snapshot().status in ("active", "blocked")


async def test_user_message_during_verification_wins(runtime_factory):
    calls = []

    async def stream(model, messages, tools, options):
        from zenagent.events import AssistantEvent
        from zenagent.types import AssistantMessage, TextContent, Usage

        calls.append(messages[-1].content)
        partial = AssistantMessage(
            content=[TextContent(text="working")],
            provider="p", model="m", usage=Usage(),
            stop_reason="stop", timestamp=0,
        )
        yield AssistantEvent(type="start", partial=partial)
        yield AssistantEvent(type="done", partial=partial, reason="stop")

    runtime = await runtime_factory(stream_fn=stream)
    verifier = GatedVerifier()
    _bind_goal(runtime, verifier)
    chat = ChatController(runtime)
    chat.start_goal("fix tests")
    await verifier.started.wait()

    await chat.submit("user during verify")
    verifier.release.set()
    await chat.wait_for_idle()

    assert "user during verify" in calls[1]
    assert "fix tests" in calls[2]


async def test_pause_during_verification_stops_continuation(runtime_factory):
    calls = []

    async def stream(model, messages, tools, options):
        from zenagent.events import AssistantEvent
        from zenagent.types import AssistantMessage, TextContent, Usage

        calls.append(messages[-1].content)
        partial = AssistantMessage(
            content=[TextContent(text="working")],
            provider="p", model="m", usage=Usage(),
            stop_reason="stop", timestamp=0,
        )
        yield AssistantEvent(type="start", partial=partial)
        yield AssistantEvent(type="done", partial=partial, reason="stop")

    runtime = await runtime_factory(stream_fn=stream)
    verifier = GatedVerifier()
    _bind_goal(runtime, verifier)
    chat = ChatController(runtime)
    chat.start_goal("fix tests")
    await verifier.started.wait()

    runtime.goal.pause()
    verifier.release.set()
    await chat.wait_for_idle()

    assert runtime.goal.snapshot().status == "paused"
    assert len(calls) == 1


async def test_session_switch_during_verification_keeps_new_session_clean(
    runtime_factory, tmp_path
):
    calls = []

    async def stream(model, messages, tools, options):
        from zenagent.events import AssistantEvent
        from zenagent.types import AssistantMessage, TextContent, Usage

        calls.append(messages[-1].content)
        partial = AssistantMessage(
            content=[TextContent(text="working")],
            provider="p", model="m", usage=Usage(),
            stop_reason="stop", timestamp=0,
        )
        yield AssistantEvent(type="start", partial=partial)
        yield AssistantEvent(type="done", partial=partial, reason="stop")

    runtime = await runtime_factory(stream_fn=stream)
    verifier = GatedVerifier()
    _bind_goal(runtime, verifier)
    chat = ChatController(runtime)
    chat.start_goal("fix tests")
    await verifier.started.wait()

    other = tmp_path / "other.jsonl"
    other.write_text("", encoding="utf-8")
    await runtime.switch_session(other)
    verifier.release.set()
    await chat.wait_for_idle()

    assert runtime.session.path == other
    assert not any(
        getattr(message, "goal", False) for message in runtime.agent.messages
    )


async def test_start_goal_while_busy_defers(runtime_factory):
    gate = asyncio.Event()

    async def stream(model, messages, tools, options):
        from zenagent.events import AssistantEvent
        from zenagent.types import AssistantMessage, TextContent, Usage

        partial = AssistantMessage(
            content=[TextContent(text="busy")],
            provider="p",
            model="m",
            usage=Usage(),
            stop_reason="stop",
            timestamp=0,
        )
        yield AssistantEvent(type="start", partial=partial)
        await gate.wait()
        yield AssistantEvent(type="done", partial=partial, reason="stop")

    runtime = await runtime_factory(stream_fn=stream)
    _bind_goal(runtime, FakeVerifier(GoalVerdict("met", "done")))
    chat = ChatController(runtime)
    task = asyncio.create_task(chat.submit("first"))
    await asyncio.sleep(0.02)

    message = chat.start_goal("later")
    assert "will start" in message
    assert runtime.goal.snapshot().status == "active"

    gate.set()
    await task
    await chat.wait_for_idle()
    assert runtime.goal.snapshot().status == "complete"
