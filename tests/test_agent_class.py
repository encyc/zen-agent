"""Agent class tests: transcript ownership, listeners, steering, abort, idle.

Every stream is a fake driven by a script, so nothing here touches the network.
"""

import asyncio
import time

import pytest

from pyagent.agent import Agent
from pyagent.agent.loop import AgentTool, ToolResultAgent
from pyagent.events import Aborted, AgentEvent, AssistantEvent
from pyagent.providers.catalog import Model
from pyagent.providers.compat import CompatFlags
from pyagent.types import (
    AssistantMessage,
    Cost,
    TextContent,
    ToolCall,
    Usage,
    UserMessage,
)


def model() -> Model:
    return Model(
        id="test-model",
        provider="test",
        api="openai-completions",
        base_url="https://api.test/v1",
        context_window=1000,
        max_tokens=100,
        reasoning=False,
        cost=Cost(),
        compat=CompatFlags(),
    )


def partial(content=None) -> AssistantMessage:
    return AssistantMessage(
        content=list(content or []),
        provider="test",
        model="test-model",
        usage=Usage(),
        stop_reason="pending",
        timestamp=int(time.time()),
    )


def scripted(*turns):
    """Build a StreamFn replaying one script per call; records every request."""

    class Stream:
        def __init__(self) -> None:
            self.turns = [list(turn) for turn in turns]
            self.requests: list[list] = []

        async def __call__(self, model, messages, tools, options):
            if not self.turns:
                raise AssertionError("unexpected extra stream call")
            self.requests.append(list(messages))
            turn = self.turns.pop(0)
            snap = partial()
            yield AssistantEvent(type="start", partial=snap)
            for kind, payload in turn:
                if kind == "text":
                    snap.content.append(TextContent(text=payload))
                    yield AssistantEvent(type="text_delta", partial=snap, delta=payload)
                elif kind == "tool":
                    call = ToolCall(id=payload, name="echo", arguments={})
                    snap.content.append(call)
                    yield AssistantEvent(
                        type="toolcall_end", partial=snap, tool_call=call
                    )
                elif kind == "end":
                    snap.stop_reason = payload
                    yield AssistantEvent(type="done", partial=snap, reason=payload)

    return Stream()


def end(reason: str = "stop"):
    return ("end", reason)


def make_agent(stream, **overrides) -> Agent:
    agent = Agent(
        model=model(),
        system_prompt="You are a test agent.",
        api_key="k",
        stream_fn=stream,
    )
    for key, value in overrides.items():
        setattr(agent, key, value)
    return agent


async def collect(agent: Agent, *prompts: str) -> list[AgentEvent]:
    events: list[AgentEvent] = []

    async def listener(event: AgentEvent) -> None:
        events.append(event)

    agent.subscribe(listener)
    for text in prompts:
        await agent.prompt(text)
    return events


# ---------------------------------------------------------------------------
# Listeners
# ---------------------------------------------------------------------------


async def test_listeners_receive_events_in_order_and_can_unsubscribe():
    stream = scripted([("text", "hi"), end()])
    agent = make_agent(stream)
    first: list[str] = []
    second: list[str] = []

    agent.subscribe(lambda event: first.append(event.type))
    unsub = agent.subscribe(lambda event: second.append(event.type))

    await agent.prompt("hello")
    assert first == second  # same events, same order
    assert first[0] == "agent_start" and first[-1] == "agent_end"

    unsub()
    first.clear()
    second.clear()
    await agent.prompt("again")
    assert second == []  # unsubscribed listener stays silent
    assert first  # the other one still works


async def test_listener_exception_does_not_break_the_run():
    stream = scripted([("text", "hi"), end()])
    agent = make_agent(stream)
    seen: list[str] = []

    def broken(event: AgentEvent) -> None:
        raise RuntimeError("bad listener")

    agent.subscribe(broken)
    agent.subscribe(lambda event: seen.append(event.type))

    await agent.prompt("hello")  # must not raise
    assert seen[-1] == "agent_end"


async def test_async_listeners_are_awaited_before_idle():
    stream = scripted([("text", "hi"), end()])
    agent = make_agent(stream)
    finished_at: list[str] = []

    async def slow(event: AgentEvent) -> None:
        if event.type == "agent_end":
            await asyncio.sleep(0.02)
            finished_at.append("listener")

    agent.subscribe(slow)
    waiter = asyncio.create_task(agent.wait_for_idle())
    await agent.prompt("hello")
    # prompt awaits the run (listeners included), so idle is already settled.
    await waiter
    assert finished_at == ["listener"]


# ---------------------------------------------------------------------------
# Transcript ownership
# ---------------------------------------------------------------------------


async def test_prompt_appends_messages_and_second_turn_sees_history():
    stream = scripted([("text", "one"), end()], [("text", "two"), end()])
    agent = make_agent(stream)

    await agent.prompt("first")
    assert [type(m) for m in agent.messages] == [UserMessage, AssistantMessage]
    await agent.prompt("second")

    assert [type(m) for m in agent.messages] == [
        UserMessage,
        AssistantMessage,
        UserMessage,
        AssistantMessage,
    ]
    second_request = stream.requests[1]
    # system prompt + the whole first exchange + the new user message
    assert len(second_request) == 4
    assert isinstance(second_request[1], UserMessage)


async def test_messages_property_is_a_copy():
    agent = make_agent(scripted([("text", "x"), end()]))
    await agent.prompt("hi")
    snapshot = agent.messages
    snapshot.clear()
    assert len(agent.messages) == 2  # external mutation cannot corrupt the agent


async def test_concurrent_prompt_is_rejected_and_allowed_after_idle():
    async def slow_stream(model, messages, tools, options):
        snap = partial()
        yield AssistantEvent(type="start", partial=snap)
        await asyncio.sleep(0.03)  # keep the run in flight
        snap.stop_reason = "stop"
        yield AssistantEvent(type="done", partial=snap, reason="stop")

    agent = make_agent(slow_stream)
    run = asyncio.create_task(agent.prompt("one"))
    await asyncio.sleep(0.005)  # let the run start

    with pytest.raises(RuntimeError, match="already running"):
        await agent.prompt("two")

    await run
    assert agent.is_idle

    agent.stream_fn = scripted([("text", "next"), end()])
    await agent.prompt("three")  # fine once idle
    assert agent.is_idle


# ---------------------------------------------------------------------------
# Steering
# ---------------------------------------------------------------------------


async def test_steer_is_injected_after_the_tool_round():
    stream = scripted(
        [("tool", "c1"), end("tool_use")],
        [("text", "done"), end()],
    )
    async def echo(name, args, abort, on_update=None):
        await asyncio.sleep(0.01)
        return ToolResultAgent(content=[TextContent(text="ok")])

    agent = make_agent(stream, tools=[AgentTool(name="echo", description="d", parameters={"type": "object"}, execute=echo)])

    async def steer_during_tool_round(event: AgentEvent) -> None:
        if event.type == "tool_execution_start":
            agent.steer("actually, check the logs")
            agent.steer("and the config")

    agent.subscribe(steer_during_tool_round)
    await agent.prompt("go")

    second_request = stream.requests[1]
    steering = second_request[-1]
    assert isinstance(steering, UserMessage)
    assert steering.content == "actually, check the logs\nand the config"


# ---------------------------------------------------------------------------
# Abort, reset, replace_messages, continue
# ---------------------------------------------------------------------------


async def test_abort_ends_the_run_as_aborted_without_raising():
    async def abortable(model, messages, tools, options):
        snap = partial()
        yield AssistantEvent(type="start", partial=snap)
        while not options.abort.aborted:
            await asyncio.sleep(0.001)
        raise Aborted("cancelled by user")

    agent = make_agent(abortable)
    run = asyncio.create_task(agent.prompt("work"))
    await asyncio.sleep(0.02)
    assert agent.is_streaming
    agent.abort()
    await run  # must not raise

    assert agent.is_idle
    assert not agent.is_streaming
    assert agent.error_message == "cancelled by user"
    final = agent.messages[-1]
    assert isinstance(final, AssistantMessage) and final.stop_reason == "aborted"


async def test_reset_clears_transcript_and_steering():
    agent = make_agent(scripted([("text", "x"), end()]))
    await agent.prompt("hi")
    agent.steer("pending")
    agent.reset()

    assert agent.messages == []
    assert agent.error_message is None
    # steering was cleared too: the next run must not inject it
    stream = scripted([("text", "y"), end()])
    agent.stream_fn = stream
    await agent.prompt("next")
    assert len(stream.requests[0]) == 2  # system + new user only


async def test_reset_and_replace_messages_rejected_while_running():
    async def slow_stream(model, messages, tools, options):
        snap = partial()
        yield AssistantEvent(type="start", partial=snap)
        await asyncio.sleep(0.05)
        snap.stop_reason = "stop"
        yield AssistantEvent(type="done", partial=snap, reason="stop")

    agent = make_agent(slow_stream)
    run = asyncio.create_task(agent.prompt("work"))
    await asyncio.sleep(0.01)
    with pytest.raises(RuntimeError):
        agent.reset()
    with pytest.raises(RuntimeError):
        agent.replace_messages([])
    await run


async def test_replace_messages_and_continue_resume_a_transcript():
    stream = scripted([("text", "resumed"), end()])
    agent = make_agent(stream)
    history = [
        UserMessage(content="old question", timestamp=1),
        AssistantMessage(
            content=[TextContent(text="old answer")],
            provider="test",
            model="test-model",
            usage=Usage(),
            stop_reason="stop",
            timestamp=2,
        ),
        UserMessage(content="follow-up", timestamp=3),
    ]
    agent.replace_messages(history)

    await agent.continue_()
    assert stream.requests[0][1:] == history  # history is sent as-is after system
    assert isinstance(agent.messages[-1], AssistantMessage)


async def test_continue_requires_a_transcript():
    agent = make_agent(scripted([("text", "x"), end()]))
    with pytest.raises(ValueError, match="empty"):
        await agent.continue_()
