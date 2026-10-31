"""Kernel-level tool deadlines: table, clamp, and loop enforcement."""

import asyncio

from test_loop import (
    ScriptedStream,
    call_tool,
    finish,
    make_config,
    make_tool,
    run,
    say,
)

from zenagent.agent.loop import ToolResultAgent
from zenagent.agent.timeouts import (
    MAX_TOOL_TIMEOUT_S,
    MIN_TOOL_TIMEOUT_S,
    TOOL_TIMEOUTS,
    clamp_timeout,
    effective_timeout,
)
from zenagent.types import TextContent


class _Declared:
    def __init__(self, timeout_s):
        self.timeout_s = timeout_s


def test_clamp_timeout_keeps_values_positive_and_bounded():
    assert clamp_timeout(0) == MIN_TOOL_TIMEOUT_S
    assert clamp_timeout(5) == 5
    assert clamp_timeout(10_000) == MAX_TOOL_TIMEOUT_S


def test_effective_timeout_prefers_the_tool_declaration():
    assert effective_timeout(_Declared(0.5), "fetch") == 0.5
    assert effective_timeout(_Declared(None), "fetch") > 0
    assert effective_timeout(_Declared(None), "web_search") > 0
    assert effective_timeout(_Declared(None), "mcp__db__query") > 0
    # bash manages its own process-group deadline
    assert effective_timeout(_Declared(None), "bash") is None
    assert effective_timeout(None, "read_file") is None


async def test_loop_turns_a_deadline_into_a_timeout_error_result():
    async def slow(name, args, abort, on_update=None):
        await asyncio.sleep(10)
        return ToolResultAgent(content=[TextContent(text="never")])

    stream = ScriptedStream(
        [
            [call_tool("t1", "slow", {}), finish("tool_use")],
            [say("done"), finish("stop")],
        ]
    )
    tool = make_tool("slow", slow, timeout_s=0.05)
    events = await run("go", stream_state(), make_config(stream, tools=[tool]))

    end = next(e for e in events if e.type == "tool_execution_end")
    assert end.is_error is True
    assert end.result.error_kind == "timeout"
    assert "timed out" in end.result.content[0].text
    # The loop continued after the timeout.
    assert len(stream.calls) == 2


async def test_a_tool_without_a_deadline_is_not_wrapped():
    async def quick(name, args, abort, on_update=None):
        await asyncio.sleep(0.02)
        return ToolResultAgent(content=[TextContent(text="ok")])

    stream = ScriptedStream(
        [
            [call_tool("t1", "quick", {}), finish("tool_use")],
            [say("done"), finish("stop")],
        ]
    )
    events = await run("go", stream_state(), make_config(stream, tools=[make_tool("quick", quick)]))
    end = next(e for e in events if e.type == "tool_execution_end")
    assert end.is_error is False
    assert end.result.content[0].text == "ok"


def stream_state():
    from zenagent.agent.loop import AgentState

    return AgentState()


async def test_tool_raising_timeout_error_without_a_deadline_does_not_crash():
    """A tool may raise TimeoutError itself; with no kernel deadline that is a
    normal tool error, not a crash."""
    async def flaky(name, args, abort, on_update=None):
        raise TimeoutError("subprocess timed out")

    stream = ScriptedStream(
        [
            [call_tool("t1", "flaky", {}), finish("tool_use")],
            [say("done"), finish("stop")],
        ]
    )
    events = await run("go", stream_state(), make_config(stream, tools=[make_tool("flaky", flaky)]))
    end = next(e for e in events if e.type == "tool_execution_end")
    assert end.is_error is True
    assert end.result.error_kind == "tool_error"
    assert "TimeoutError" in end.result.content[0].text
    assert len(stream.calls) == 2


async def test_loop_applies_the_name_based_timeout_table(monkeypatch):
    monkeypatch.setitem(TOOL_TIMEOUTS, "tbl_tool", 0.05)

    async def slow(name, args, abort, on_update=None):
        await asyncio.sleep(10)
        return ToolResultAgent(content=[TextContent(text="never")])

    stream = ScriptedStream(
        [
            [call_tool("t1", "tbl_tool", {}), finish("tool_use")],
            [say("done"), finish("stop")],
        ]
    )
    events = await run("go", stream_state(), make_config(stream, tools=[make_tool("tbl_tool", slow)]))
    end = next(e for e in events if e.type == "tool_execution_end")
    assert end.is_error and end.result.error_kind == "timeout"


def test_web_search_deadline_allows_the_provider_chain():
    # Up to five providers at ~20s each, so a 25s kernel deadline would abort
    # the fallback chain instead of letting it reach the keyless last resort.
    assert TOOL_TIMEOUTS["web_search"] >= 100
