"""Agent loop tests: every scenario runs against a fake stream fn.

The fake (``ScriptedStream``) mimics the real adapter contract: it builds one
``AssistantMessage(stop_reason="pending")`` per call, mutates it as the script
advances, and yields ``AssistantEvent``s that all reference that same shared
partial. Scripts record what the loop actually sent (messages, tools, options).
No network is ever touched.
"""

import asyncio
import time
from collections.abc import Callable

from zenagent.agent import (
    AgentConfig,
    AgentState,
    AgentTool,
    ToolResultAgent,
    agent_loop,
)
from zenagent.events import AbortToken, AgentEvent, AssistantEvent
from zenagent.providers.catalog import Model
from zenagent.providers.compat import CompatFlags
from zenagent.providers.openai_completions import StreamOptions
from zenagent.types import (
    AssistantMessage,
    Cost,
    Message,
    SystemMessage,
    TextContent,
    ToolCall,
    ToolDef,
    ToolResultMessage,
    Usage,
    UserMessage,
)

# ---------------------------------------------------------------------------
# Fakes and helpers
# ---------------------------------------------------------------------------


def _model() -> Model:
    return Model(
        id="test-model",
        provider="test",
        api="openai-completions",
        base_url="https://api.test/v1",
        context_window=1000,
        max_tokens=100,
        reasoning=False,
        cost=Cost(input=0.0, output=0.0),
        compat=CompatFlags(),
    )


#: A script step mutates the shared partial and returns the events to yield.
Step = Callable[[AssistantMessage, StreamOptions], list]


def say(text: str) -> Step:
    """Emit a text block: start, one delta, end."""

    def step(partial: AssistantMessage, options: StreamOptions) -> list:
        index = len(partial.content)
        partial.content.append(TextContent(text))
        return [
            AssistantEvent(type="text_start", partial=partial, content_index=index),
            AssistantEvent(type="text_delta", partial=partial, delta=text, content_index=index),
            AssistantEvent(type="text_end", partial=partial, content=text, content_index=index),
        ]

    return step


def call_tool(
    call_id: str,
    name: str,
    arguments: dict,
    arguments_error: str | None = None,
) -> Step:
    """Emit a toolcall block: start then end."""

    def step(partial: AssistantMessage, options: StreamOptions) -> list:
        tool_call = ToolCall(id=call_id, name=name, arguments=arguments, arguments_error=arguments_error)
        index = len(partial.content)
        partial.content.append(tool_call)
        return [
            AssistantEvent(type="toolcall_start", partial=partial, content_index=index, tool_call=tool_call),
            AssistantEvent(type="toolcall_end", partial=partial, content_index=index, tool_call=tool_call),
        ]

    return step


def finish(reason: str = "stop") -> Step:
    """Emit the terminal event for ``reason`` (done, or error for error/aborted)."""

    def step(partial: AssistantMessage, options: StreamOptions) -> list:
        partial.stop_reason = reason
        event_type = "error" if reason in ("error", "aborted") else "done"
        return [AssistantEvent(type=event_type, partial=partial, reason=reason)]

    return step


def abort_mid_stream() -> Step:
    """Fire the abort token, then terminate the stream as aborted (adapter style)."""

    def step(partial: AssistantMessage, options: StreamOptions) -> list:
        assert options.abort is not None, "abort_mid_stream requires an abort token"
        options.abort.abort()
        partial.stop_reason = "aborted"
        return [AssistantEvent(type="error", partial=partial, reason="aborted")]

    return step


class ScriptedStream:
    """Scripted StreamFn: each call replays the next script; all calls recorded."""

    def __init__(self, scripts: list[list[Step]]) -> None:
        self.scripts = [list(script) for script in scripts]
        self.calls: list[dict] = []

    async def __call__(self, model, messages, tools, options):
        if not self.scripts:
            raise AssertionError("unexpected extra stream call: no script left")
        self.calls.append(
            {"messages": list(messages), "tools": list(tools), "options": options}
        )
        script = self.scripts.pop(0)
        partial = AssistantMessage(
            content=[],
            provider=model.provider,
            model=model.id,
            usage=Usage(),
            stop_reason="pending",
            timestamp=int(time.time()),
        )
        yield AssistantEvent(type="start", partial=partial)
        for step in script:
            for event in step(partial, options):
                yield event


def make_config(stream: ScriptedStream, **overrides) -> AgentConfig:
    config = AgentConfig(model=_model(), system_prompt="You are a test agent.", stream_fn=stream)
    for key, value in overrides.items():
        setattr(config, key, value)
    return config


def make_tool(name: str, execute: Callable, execution_mode: str = "parallel") -> AgentTool:
    return AgentTool(
        name=name,
        description=f"{name} tool",
        parameters={"type": "object", "properties": {}},
        execution_mode=execution_mode,
        execute=execute,
    )


async def run(prompt, state, config, abort=None) -> list[AgentEvent]:
    return [event async for event in agent_loop(prompt, state, config, abort)]


def types_of(events: list[AgentEvent]) -> list[str]:
    return [event.type for event in events]


# ---------------------------------------------------------------------------
# Scenario 1: pure text round
# ---------------------------------------------------------------------------


async def test_pure_text_event_sequence_and_state():
    stream = ScriptedStream([[say("Hello"), finish("stop")]])
    state = AgentState()
    events = await run("hi", state, make_config(stream))

    assert types_of(events) == [
        "agent_start",
        "message_start",
        "message_end",
        "turn_start",
        "message_start",
        *["message_update"] * 5,  # start + text_start/delta/end + done
        "message_end",
        "turn_end",
        "agent_end",
    ]

    # The prompt user message got start/end events and landed in state.
    assert isinstance(events[1].message, UserMessage)
    assert events[1].message.content == "hi"
    assert events[2].message is events[1].message

    # The final assistant message is complete and in state.
    final = events[-3].message
    assert isinstance(final, AssistantMessage)
    assert final.content == [TextContent(text="Hello")]
    assert final.stop_reason == "stop"

    assert len(state.messages) == 2
    assert isinstance(state.messages[0], UserMessage)
    assert state.messages[1] is final

    # agent_end carries everything added this run.
    assert events[-1].new_messages == state.messages

    # The provider saw system + user, and state never saw the system message.
    assert len(stream.calls) == 1
    sent = stream.calls[0]["messages"]
    assert isinstance(sent[0], SystemMessage)
    assert sent[0].content == "You are a test agent."
    assert isinstance(sent[1], UserMessage)
    assert not any(isinstance(m, SystemMessage) for m in state.messages)


# ---------------------------------------------------------------------------
# Scenario 2: tool round-trip with a second stream call
# ---------------------------------------------------------------------------


async def test_tool_round_trip_calls_stream_again():
    async def echo(name: str, args: dict, abort, on_update=None) -> ToolResultAgent:
        return ToolResultAgent(content=[TextContent(text=args["text"])])

    stream = ScriptedStream(
        [
            [call_tool("t1", "echo", {"text": "hi"}), finish("tool_use")],
            [say("done"), finish("stop")],
        ]
    )
    state = AgentState()
    events = await run("use the tool", state, make_config(stream, tools=[make_tool("echo", echo)]))
    types = types_of(events)

    # Tool execution bracketed the call with the right payloads.
    start = events[types.index("tool_execution_start")]
    assert start.tool_call_id == "t1"
    assert start.tool_name == "echo"
    assert start.args == {"text": "hi"}
    end = events[types.index("tool_execution_end")]
    assert end.tool_call_id == "t1"
    assert end.result.content == [TextContent(text="hi")]
    assert end.is_error is False
    assert isinstance(end.duration_ms, int)
    assert end.duration_ms >= 0

    # A ToolResultMessage was appended and wrapped in message events.
    result_message = state.messages[2]
    assert isinstance(result_message, ToolResultMessage)
    assert result_message.tool_call_id == "t1"
    assert result_message.tool_name == "echo"
    assert result_message.content == [TextContent(text="hi")]
    assert result_message.is_error is False
    assert types.count("message_start") == 4  # user, assistant1, result, assistant2
    assert types.count("message_end") == 4

    # The second round saw the tool result, and the loop ended on stop.
    assert len(stream.calls) == 2
    assert stream.calls[1]["messages"][3] is result_message
    assert isinstance(state.messages[3], AssistantMessage)
    assert state.messages[3].stop_reason == "stop"

    # ToolDefs were passed through.
    assert stream.calls[0]["tools"] == [
        ToolDef(name="echo", description="echo tool", parameters={"type": "object", "properties": {}})
    ]


# ---------------------------------------------------------------------------
# Scenario 3: tool raising RuntimeError
# ---------------------------------------------------------------------------


async def test_tool_exception_becomes_error_result_and_loop_continues():
    async def boom(name: str, args: dict, abort, on_update=None) -> ToolResultAgent:
        raise RuntimeError("kaput")

    stream = ScriptedStream(
        [
            [call_tool("t1", "boom", {}), finish("tool_use")],
            [say("recovered"), finish("stop")],
        ]
    )
    state = AgentState()
    events = await run("go", state, make_config(stream, tools=[make_tool("boom", boom)]))
    types = types_of(events)

    end = events[types.index("tool_execution_end")]
    assert end.is_error is True
    assert end.result.is_error is True
    assert end.result.content[0].text.startswith("RuntimeError: kaput")

    result_message = state.messages[2]
    assert isinstance(result_message, ToolResultMessage)
    assert result_message.is_error is True
    assert result_message.content[0].text.startswith("RuntimeError: kaput")

    # The loop continued: second round ran and finished with stop.
    assert len(stream.calls) == 2
    assert state.messages[-1].stop_reason == "stop"


# ---------------------------------------------------------------------------
# Scenario 4: before_tool_call blocks execution
# ---------------------------------------------------------------------------


async def test_before_tool_call_blocks_execution():
    executed = []

    async def echo(name: str, args: dict, abort, on_update=None) -> ToolResultAgent:
        executed.append(name)
        return ToolResultAgent(content=[TextContent(text="never")])

    def guard(tool_call, args, tool=None):
        return {"block": True, "reason": "not allowed"}

    stream = ScriptedStream(
        [
            [call_tool("t1", "echo", {"x": 1}), finish("tool_use")],
            [say("ok"), finish("stop")],
        ]
    )
    state = AgentState()
    events = await run("go", state, make_config(stream, tools=[make_tool("echo", echo)], before_tool_call=guard))
    types = types_of(events)

    assert executed == []  # execute was never called

    end = events[types.index("tool_execution_end")]
    assert end.is_error is True
    assert end.result.content == [TextContent(text="not allowed")]

    result_message = state.messages[2]
    assert isinstance(result_message, ToolResultMessage)
    assert result_message.is_error is True
    assert result_message.content[0].text == "not allowed"

    assert len(stream.calls) == 2  # loop continued after the blocked call


# ---------------------------------------------------------------------------
# Scenario 5: abort terminates the stream, agent_end still emitted
# ---------------------------------------------------------------------------


async def test_abort_mid_stream_terminates_without_tools():
    async def echo(name: str, args: dict, abort, on_update=None) -> ToolResultAgent:
        return ToolResultAgent(content=[TextContent(text="late")])

    stream = ScriptedStream(
        [
            [
                call_tool("t1", "echo", {"x": 1}),  # model asked for a tool...
                abort_mid_stream(),  # ...but the user aborted before execution
            ]
        ]
    )
    token = AbortToken()
    state = AgentState()
    events = await run("go", state, make_config(stream, tools=[make_tool("echo", echo)]), abort=token)
    types = types_of(events)

    # Aborted terminal: no tool execution at all.
    assert "tool_execution_start" not in types
    assert "tool_execution_end" not in types
    assert not any(isinstance(m, ToolResultMessage) for m in state.messages)

    final = state.messages[-1]
    assert final.stop_reason == "aborted"

    # agent_end still emitted, after message_end/turn_end.
    assert types[-4:] == ["message_update", "message_end", "turn_end", "agent_end"]
    assert types.count("agent_end") == 1
    assert events[-1].new_messages == state.messages
    assert len(stream.calls) == 1


async def test_abort_before_loop_runs_no_stream_call():
    stream = ScriptedStream([[say("never"), finish("stop")]])
    token = AbortToken()
    token.abort()
    state = AgentState()
    events = await run("go", state, make_config(stream), abort=token)
    # Prompt message still lands, then the loop-top abort check bails out.
    assert types_of(events) == ["agent_start", "message_start", "message_end", "agent_end"]
    assert stream.calls == []
    assert len(state.messages) == 1
    assert events[-1].new_messages == state.messages


# ---------------------------------------------------------------------------
# Scenario 6: steering injects messages after the tool round
# ---------------------------------------------------------------------------


async def test_steering_messages_appended_after_tool_round():
    async def echo(name: str, args: dict, abort, on_update=None) -> ToolResultAgent:
        return ToolResultAgent(content=[TextContent(text="ok")])

    steering_calls = []

    async def steer() -> list[Message]:
        steering_calls.append(time.monotonic())
        # first poll happens at run start, the second one after the tool round
        if len(steering_calls) == 2:
            return [UserMessage(content="new info", timestamp=int(time.time()))]
        return []

    stream = ScriptedStream(
        [
            [call_tool("t1", "echo", {}), finish("tool_use")],
            [say("final"), finish("stop")],
        ]
    )
    state = AgentState()
    events = await run("go", state, make_config(stream, tools=[make_tool("echo", echo)], get_steering_messages=steer))
    types = types_of(events)

    # Steering message sits between the tool result and the second assistant.
    assert isinstance(state.messages[3], UserMessage)
    assert state.messages[3].content == "new info"
    assert isinstance(state.messages[4], AssistantMessage)

    # The second stream call saw it.
    assert len(stream.calls) == 2
    assert stream.calls[1]["messages"][4] is state.messages[3]

    # Steering is consulted at run start and after each tool round (twice
    # here); the final text round breaks on stop without a third poll.
    assert len(steering_calls) == 2
    assert types.count("message_start") == 5  # user, a1, result, steer, a2
    assert events[-1].new_messages == state.messages


# ---------------------------------------------------------------------------
# Load-bearing binding details
# ---------------------------------------------------------------------------


async def test_thinking_off_normalized_to_none():
    stream = ScriptedStream([[say("a"), finish("stop")]])
    await run("go", AgentState(), make_config(stream, thinking="off"))
    assert stream.calls[0]["options"].thinking is None

    stream = ScriptedStream([[say("a"), finish("stop")]])
    await run("go", AgentState(), make_config(stream, thinking="high"))
    assert stream.calls[0]["options"].thinking == "high"

    stream = ScriptedStream([[say("a"), finish("stop")]])
    await run("go", AgentState(), make_config(stream))  # default None
    assert stream.calls[0]["options"].thinking is None


async def test_stream_options_carry_api_key_session_and_abort():
    stream = ScriptedStream([[say("a"), finish("stop")]])
    token = AbortToken()
    await run("go", AgentState(), make_config(stream, api_key="sk-x", session_id="sess-1"), abort=token)
    options = stream.calls[0]["options"]
    assert isinstance(options, StreamOptions)
    assert options.api_key == "sk-x"
    assert options.session_id == "sess-1"
    assert options.abort is token


async def test_transform_context_applies_to_request_only():
    async def identity(name, args, abort, on_update=None):
        return ToolResultAgent(content=[])

    captured = []

    async def transform(request: list[Message]) -> list[Message]:
        captured.append(list(request))
        return request[1:]  # pretend the system message and old history vanish

    stream = ScriptedStream([[say("a"), finish("stop")]])
    state = AgentState()
    config = make_config(stream, transform_context=transform)
    await run("go", state, config)

    # Hook saw the FULL request list: system + user = 2, system first.
    assert isinstance(captured[0][0], SystemMessage)
    assert len(captured[0]) == 2

    # The stream saw the hook's return value (system dropped); state kept everything.
    assert len(stream.calls[0]["messages"]) == 1
    assert len(state.messages) == 2


async def test_parallel_tools_results_in_source_order_despite_completion():
    log = []

    async def slow(name: str, args: dict, abort, on_update=None) -> ToolResultAgent:
        log.append("slow:start")
        await asyncio.sleep(0.05)
        log.append("slow:end")
        return ToolResultAgent(content=[TextContent(text="slow-done")])

    async def fast(name: str, args: dict, abort, on_update=None) -> ToolResultAgent:
        log.append("fast:start")
        await asyncio.sleep(0.001)
        log.append("fast:end")
        return ToolResultAgent(content=[TextContent(text="fast-done")])

    stream = ScriptedStream(
        [
            [
                call_tool("t1", "slow", {}),  # first in content, last to finish
                call_tool("t2", "fast", {}),
                finish("tool_use"),
            ],
            [say("done"), finish("stop")],
        ]
    )
    state = AgentState()
    events = await run(
        "go",
        state,
        make_config(stream, tools=[make_tool("slow", slow), make_tool("fast", fast)]),
    )

    # They really ran concurrently: fast finished before slow.
    assert log == ["slow:start", "fast:start", "fast:end", "slow:end"]

    # But results landed in assistant source order.
    assert state.messages[2].tool_call_id == "t1"
    assert state.messages[2].content[0].text == "slow-done"
    assert state.messages[3].tool_call_id == "t2"
    assert state.messages[3].content[0].text == "fast-done"

    # And so did the execution events.
    ends = [e for e in events if e.type == "tool_execution_end"]
    assert [e.tool_call_id for e in ends] == ["t1", "t2"]


async def test_sequential_tools_serialize_in_call_order():
    log = []

    def make(name):
        async def run_it(n, args, abort, on_update=None):
            log.append(f"{name}:start")
            await asyncio.sleep(0.001)
            log.append(f"{name}:end")
            return ToolResultAgent(content=[TextContent(text=f"{name}-done")])

        return run_it

    stream = ScriptedStream(
        [
            [call_tool("q1", "q1", {}), call_tool("q2", "q2", {}), finish("tool_use")],
            [say("done"), finish("stop")],
        ]
    )
    state = AgentState()
    await run(
        "go",
        state,
        make_config(
            stream,
            tools=[make_tool("q1", make("q1"), execution_mode="sequential"), make_tool("q2", make("q2"), execution_mode="sequential")],
        ),
    )
    assert log == ["q1:start", "q1:end", "q2:start", "q2:end"]


async def test_unknown_tool_is_error_result_and_loop_continues():
    stream = ScriptedStream(
        [
            [call_tool("t1", "ghost", {}), finish("tool_use")],
            [say("ok"), finish("stop")],
        ]
    )
    state = AgentState()
    events = await run("go", state, make_config(stream))  # no tools registered
    types = types_of(events)

    end = events[types.index("tool_execution_end")]
    assert end.is_error is True
    assert end.result.content[0].text == "unknown tool: ghost"
    assert isinstance(state.messages[2], ToolResultMessage)
    assert state.messages[2].is_error is True
    assert len(stream.calls) == 2


async def test_arguments_error_and_non_dict_arguments_become_error_results():
    async def noop(name, args, abort, on_update=None):
        return ToolResultAgent(content=[TextContent(text="ran")])

    stream = ScriptedStream(
        [
            [
                call_tool("t1", "noop", {}, arguments_error="invalid JSON at line 1"),
                finish("tool_use"),
            ],
            [say("ok"), finish("stop")],
        ]
    )
    state = AgentState()
    events = await run("go", state, make_config(stream, tools=[make_tool("noop", noop)]))
    types = types_of(events)
    end = events[types.index("tool_execution_end")]
    assert end.is_error is True
    assert end.result.content[0].text == "invalid JSON at line 1"

    # Non-dict arguments: same treatment, loop keeps going.
    stream = ScriptedStream(
        [
            [call_tool("t2", "noop", "not-a-dict"), finish("tool_use")],  # type: ignore[arg-type]
            [say("ok"), finish("stop")],
        ]
    )
    state = AgentState()
    events = await run("go", state, make_config(stream, tools=[make_tool("noop", noop)]))
    types = types_of(events)
    end = events[types.index("tool_execution_end")]
    assert end.is_error is True
    assert len(stream.calls) == 2


async def test_after_tool_call_can_replace_or_patch_result():
    async def echo(name, args, abort, on_update=None):
        return ToolResultAgent(content=[TextContent(text="raw")])

    # Replacement with terminate=True ends the run after this round.
    async def replacer(call, result):
        return ToolResultAgent(content=[TextContent(text="replaced")], terminate=True)

    stream = ScriptedStream([[call_tool("t1", "echo", {}), finish("tool_use")]])
    state = AgentState()
    events = await run(
        "go", state, make_config(stream, tools=[make_tool("echo", echo)], after_tool_call=replacer)
    )
    types = types_of(events)
    end = events[types.index("tool_execution_end")]
    assert end.result.content[0].text == "replaced"
    assert state.messages[2].content[0].text == "replaced"
    assert len(stream.calls) == 1  # terminate=True stopped the loop
    assert types[-1] == "agent_end"

    # Dict partial-merge only touches the listed fields.
    def patcher(call, result):
        return {"is_error": True}

    stream = ScriptedStream(
        [
            [call_tool("t1", "echo", {}), finish("tool_use")],
            [say("ok"), finish("stop")],
        ]
    )
    state = AgentState()
    events = await run(
        "go", state, make_config(stream, tools=[make_tool("echo", echo)], after_tool_call=patcher)
    )
    types = types_of(events)
    end = events[types.index("tool_execution_end")]
    assert end.result.content[0].text == "raw"  # content untouched
    assert end.is_error is True  # patched
    assert len(stream.calls) == 2  # terminate untouched -> loop continued


async def test_before_tool_call_receives_the_resolved_tool():
    """Hooks need the AgentTool (tier, name) to make policy decisions."""
    seen = []

    async def echo(name, args, abort, on_update=None):
        return ToolResultAgent(content=[TextContent(text="ran")])

    def guard(call, args, tool=None):
        seen.append(tool)

    stream = ScriptedStream(
        [
            [call_tool("t1", "echo", {}), finish("tool_use")],
            [say("ok"), finish("stop")],
        ]
    )
    state = AgentState()
    await run("go", state, make_config(stream, tools=[make_tool("echo", echo)], before_tool_call=guard))

    assert len(seen) == 1
    assert isinstance(seen[0], AgentTool)
    assert seen[0].name == "echo"


async def test_async_before_tool_call_hook_is_awaited():
    async def echo(name, args, abort, on_update=None):
        return ToolResultAgent(content=[TextContent(text="ran")])

    async def guard(call, args, tool=None):
        return {"block": True, "reason": "async block"}

    stream = ScriptedStream(
        [
            [call_tool("t1", "echo", {}), finish("tool_use")],
            [say("ok"), finish("stop")],
        ]
    )
    state = AgentState()
    events = await run(
        "go", state, make_config(stream, tools=[make_tool("echo", echo)], before_tool_call=guard)
    )
    types = types_of(events)
    end = events[types.index("tool_execution_end")]
    assert end.is_error is True
    assert end.result.content[0].text == "async block"


async def test_degenerate_tool_call_skipped_and_terminate_all_breaks():
    async def echo(name, args, abort, on_update=None):
        return ToolResultAgent(content=[TextContent(text="ok")], terminate=True)

    # A call with empty id AND empty name is skipped entirely.
    stream = ScriptedStream(
        [
            [
                call_tool("", "", {}),  # degenerate: skipped
                call_tool("t1", "echo", {}),
                finish("tool_use"),
            ]
        ]
    )
    state = AgentState()
    events = await run("go", state, make_config(stream, tools=[make_tool("echo", echo)]))
    types = types_of(events)

    assert types.count("tool_execution_start") == 1
    assert len(state.messages) == 3  # user, assistant, single tool result
    # Every finalized result terminate=True -> break; no second round.
    assert len(stream.calls) == 1
    assert types[-1] == "agent_end"


async def test_prompt_none_resumes_existing_state():
    prior_user = UserMessage(content="earlier", timestamp=1)
    stream = ScriptedStream([[say("fresh"), finish("stop")]])
    state = AgentState(messages=[prior_user])
    events = await run(None, state, make_config(stream))

    assert types_of(events)[0:1] == ["agent_start"]
    assert "message_start" not in types_of(events)[:2]  # no user-message events
    sent = stream.calls[0]["messages"]
    assert len(sent) == 2
    assert isinstance(sent[0], SystemMessage)
    assert sent[1] is prior_user
    assert len(state.messages) == 2
    assert state.messages[0] is prior_user
    assert events[-1].new_messages == [state.messages[1]]


# ---------------------------------------------------------------------------
# Task 9A: truncated responses, progress updates, schema validation
# ---------------------------------------------------------------------------


def tool_call_ids(message: AssistantMessage) -> list[str]:
    return [b.id for b in message.content if isinstance(b, ToolCall)]


async def test_length_stop_with_tool_calls_fails_batch_without_executing():
    """Truncated tool calls must not run, and must still be paired with results."""
    executed: list[dict] = []

    async def echo(name, args, abort, on_update=None):
        executed.append(args)
        return ToolResultAgent(content=[TextContent(text="ran")])

    stream = ScriptedStream(
        [
            [
                call_tool("c1", "echo", {"text": "a"}),
                call_tool("c2", "echo", {"text": "b"}),
                finish("length"),
            ],
            [say("re-issued"), finish("stop")],
        ]
    )
    state = AgentState()
    events = await run("go", state, make_config(stream, tools=[make_tool("echo", echo)]))

    assert executed == []  # nothing truncated ever reached the tool
    ends = [e for e in events if e.type == "tool_execution_end"]
    assert len(ends) == 2
    assert all(e.is_error for e in ends)
    assert all("output token limit" in e.result.content[0].text for e in ends)

    # The transcript keeps every tool_call paired with a tool result.
    assistant = next(m for m in state.messages if isinstance(m, AssistantMessage))
    calls = tool_call_ids(assistant)
    results = [
        m.tool_call_id for m in state.messages if isinstance(m, ToolResultMessage)
    ]
    assert calls == ["c1", "c2"]
    assert results == ["c1", "c2"]
    # The model got a chance to re-issue: a second request was made and finished.
    assert len(stream.calls) == 2
    assert state.messages[-1].stop_reason == "stop"


async def test_tool_progress_updates_are_forwarded_and_late_ones_dropped():
    def part(text: str) -> ToolResultAgent:
        return ToolResultAgent(content=[TextContent(text=text)])

    async def slow(name, args, abort, on_update=None):
        for i in range(4):
            on_update(part(f"slow-{i}"))
            await asyncio.sleep(0.03)
        return ToolResultAgent(content=[TextContent(text="slow-done")])

    async def fast(name, args, abort, on_update=None):
        async def late() -> None:
            await asyncio.sleep(0.05)  # fast has already settled by now
            on_update(part("late-from-fast"))

        asyncio.create_task(late())
        on_update(part("fast-now"))
        return ToolResultAgent(content=[TextContent(text="fast-done")])

    stream = ScriptedStream(
        [
            [call_tool("c1", "fast", {}), call_tool("c2", "slow", {}), finish("tool_use")],
            [say("ok"), finish("stop")],
        ]
    )
    state = AgentState()
    events = await run(
        "go", state, make_config(stream, tools=[make_tool("fast", fast), make_tool("slow", slow)])
    )

    updates = [e for e in events if e.type == "tool_execution_update"]
    texts = [u.partial_result.content[0].text for u in updates]
    assert "fast-now" in texts
    assert texts.count("slow-0") == 1
    assert "late-from-fast" not in texts  # settled tools cannot stream anymore
    assert all(u.tool_call_id in {"c1", "c2"} for u in updates)
    assert {u.tool_name for u in updates} == {"fast", "slow"}


async def test_schema_invalid_arguments_are_not_executed():
    executed: list[dict] = []

    async def typed(name, args, abort, on_update=None):
        executed.append(args)
        return ToolResultAgent(content=[TextContent(text="ran")])

    tool = AgentTool(
        name="typed",
        description="typed tool",
        parameters={
            "type": "object",
            "properties": {"n": {"type": "integer"}},
            "required": ["n"],
        },
        execute=typed,
    )
    stream = ScriptedStream(
        [
            [call_tool("c1", "typed", {"n": "not-an-int"}), finish("tool_use")],
            [call_tool("c2", "typed", {}), finish("tool_use")],
            [say("ok"), finish("stop")],
        ]
    )
    state = AgentState()
    events = await run("go", state, make_config(stream, tools=[tool]))

    assert executed == []
    ends = [e for e in events if e.type == "tool_execution_end"]
    assert len(ends) == 2 and all(e.is_error for e in ends)
    assert "must be a integer" in ends[0].result.content[0].text
    assert "missing required argument" in ends[1].result.content[0].text


async def test_repeated_truncation_stops_instead_of_reasking_forever():
    """A model stuck on length truncation gets a bounded number of re-issues."""

    async def echo(name, args, abort, on_update=None):  # pragma: no cover - never runs
        raise AssertionError("truncated tool calls must never execute")

    # Script enough truncating turns to exceed the cap; the loop must stop early.
    script = [
        [call_tool("c1", "echo", {}), finish("length")]
        for _ in range(10)
    ]
    stream = ScriptedStream(script)
    state = AgentState()
    await run("go", state, make_config(stream, tools=[make_tool("echo", echo)]))

    assert len(stream.calls) == 2, "expected exactly two truncation re-issues"
    # Every attempt still left a properly paired transcript.
    calls = [
        b.id
        for m in state.messages
        if isinstance(m, AssistantMessage)
        for b in m.content
        if isinstance(b, ToolCall)
    ]
    results = [
        m.tool_call_id for m in state.messages if isinstance(m, ToolResultMessage)
    ]
    assert calls == results == ["c1", "c1"]


async def test_steering_queued_before_the_run_is_injected_at_start():
    """Typed-while-waiting input must reach the very first request."""
    stream = ScriptedStream([[say("ok"), finish("stop")]])
    queued = [UserMessage(content="earlier note", timestamp=1)]

    async def steering():
        found, queued[:] = list(queued), []
        return found

    state = AgentState()
    events = await run("go", state, make_config(stream, get_steering_messages=steering))

    first_request = stream.calls[0]["messages"]
    assert [type(m).__name__ for m in first_request] == ["SystemMessage", "UserMessage", "UserMessage"]
    assert first_request[2].content == "earlier note"
    assert any(
        event.type == "message_end" and isinstance(event.message, UserMessage)
        and event.message.content == "earlier note"
        for event in events
    )


# ---------------------------------------------------------------------------
# S1-T3: turn and cost guardrails
# ---------------------------------------------------------------------------


class LoopingToolStream:
    """Always asks for another tool call; records request count and spend."""

    def __init__(self, cost_per_turn: float = 0.0) -> None:
        self.cost_per_turn = cost_per_turn
        self.calls = 0

    async def __call__(self, model, messages, tools, options):
        self.calls += 1
        partial = AssistantMessage(
            content=[],
            provider=model.provider,
            model=model.id,
            usage=Usage(cost=Cost(total=self.cost_per_turn)),
            stop_reason="pending",
            timestamp=0,
        )
        yield AssistantEvent(type="start", partial=partial)
        call = ToolCall(id=f"c{self.calls}", name="echo", arguments={})
        partial.content.append(call)
        yield AssistantEvent(type="toolcall_end", partial=partial, tool_call=call)
        partial.stop_reason = "tool_use"
        yield AssistantEvent(type="done", partial=partial, reason="tool_use")


async def test_max_turns_stops_before_the_next_request():
    stream = LoopingToolStream()

    async def echo(name, args, abort, on_update=None):
        return ToolResultAgent(content=[TextContent(text="ok")])

    state = AgentState()
    await run(
        "go",
        state,
        make_config(
            stream,
            tools=[make_tool("echo", echo)],
            max_turns=2,
        ),
    )
    assert stream.calls == 2
    assert state.termination == "max_turns"
    calls = [b.id for m in state.messages if isinstance(m, AssistantMessage) for b in m.content if isinstance(b, ToolCall)]
    results = [m.tool_call_id for m in state.messages if isinstance(m, ToolResultMessage)]
    assert calls == results == ["c1", "c2"]


async def test_max_cost_stops_after_the_turn_that_crosses_it():
    stream = LoopingToolStream(cost_per_turn=0.01)

    async def echo(name, args, abort, on_update=None):
        return ToolResultAgent(content=[TextContent(text="ok")])

    state = AgentState()
    await run(
        "go",
        state,
        make_config(stream, tools=[make_tool("echo", echo)], max_cost=0.005),
    )
    assert stream.calls == 1  # budget check happens before the next request
    assert state.termination == "max_cost"
    calls = [b.id for m in state.messages if isinstance(m, AssistantMessage) for b in m.content if isinstance(b, ToolCall)]
    results = [m.tool_call_id for m in state.messages if isinstance(m, ToolResultMessage)]
    assert calls == results == ["c1"]  # transcript still paired


async def test_default_guardrails_do_not_change_normal_runs():
    stream = ScriptedStream([[say("done"), finish("stop")]])
    state = AgentState()
    await run("go", state, make_config(stream))
    assert state.termination == "stop"


async def test_termination_records_abort_and_error():
    aborted = ScriptedStream([[say("x"), finish("aborted")]])
    state = AgentState()
    await run("go", state, make_config(aborted))
    assert state.termination == "aborted"

    failed = ScriptedStream([[say("x"), finish("error")]])
    state = AgentState()
    await run("go", state, make_config(failed))
    assert state.termination == "error"


# ---------------------------------------------------------------------------
# Scenario: the kernel result budget is applied after the tool returns
# ---------------------------------------------------------------------------


async def test_tool_result_budget_caps_oversized_output():
    payload = "q" * 50_000

    async def big(name, args, abort, on_update=None):
        return ToolResultAgent(content=[TextContent(text=payload)])

    stream = ScriptedStream(
        [
            [call_tool("t1", "big", {}), finish("tool_use")],
            [say("ok"), finish("stop")],
        ]
    )
    state = AgentState()
    events = await run("go", state, make_config(stream, tools=[make_tool("big", big)]))
    types = types_of(events)

    end = events[types.index("tool_execution_end")]
    rendered = end.result.content[0].text
    assert len(rendered) < len(payload)
    assert "truncated" in rendered

    result_message = state.messages[2]
    assert isinstance(result_message, ToolResultMessage)
    assert result_message.content[0].text == rendered


async def test_two_argument_hooks_keep_working():
    """v0.3 library users may pass a 2-arg hook; that must not crash the run."""
    seen = []

    async def echo(name, args, abort, on_update=None):
        return ToolResultAgent(content=[TextContent(text="ran")])

    def legacy_guard(call, args):
        seen.append(call.name)
        return {"block": True, "reason": "legacy hook"}

    stream = ScriptedStream(
        [
            [call_tool("t1", "echo", {}), finish("tool_use")],
            [say("ok"), finish("stop")],
        ]
    )
    state = AgentState()
    events = await run(
        "go",
        state,
        make_config(stream, tools=[make_tool("echo", echo)], before_tool_call=legacy_guard),
    )
    types = types_of(events)
    end = events[types.index("tool_execution_end")]
    assert seen == ["echo"]
    assert end.is_error is True
    assert end.result.content[0].text == "legacy hook"
