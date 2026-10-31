"""Define an AgentTool and drive a real Agent loop with a fake stream (offline).

Two pieces are worth copying out of here:

* ``ADD`` is an ``AgentTool``: a name, a JSON Schema, and an async
  ``execute(call_id, args, abort, on_update)`` returning a ``ToolResultAgent``.
  ``approval="read"`` is the capability declaration the harness permission gate
  reads (read tools never need confirmation).
* ``offline_stream()`` is a ``StreamFn`` that never touches the network. Turn 1
  asks for the tool, turn 2 answers from its result -- the production loop, with
  no provider and no API key. Tests use the same trick (``docs/testing.md``).

    uv run python examples/custom_tool.py
"""

from __future__ import annotations

import asyncio

from zenagent.agent import Agent
from zenagent.agent.loop import AgentTool, ToolResultAgent
from zenagent.events import AssistantEvent
from zenagent.providers.catalog import Model
from zenagent.providers.compat import CompatFlags
from zenagent.types import (
    AssistantMessage,
    Cost,
    Message,
    TextContent,
    ToolCall,
    ToolResultMessage,
    Usage,
)


async def add(call_id: str, args: dict, abort, on_update=None) -> ToolResultAgent:
    """The tool body. ``content`` goes to the model; ``details`` only to the UI."""
    total = int(args.get("a", 0)) + int(args.get("b", 0))
    return ToolResultAgent(content=[TextContent(text=str(total))], details={"sum": total})


ADD = AgentTool(
    name="add",
    description="Add two integers.",
    parameters={
        "type": "object",
        "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
        "required": ["a", "b"],
    },
    execute=add,
    approval="read",
)

MODEL = Model(
    id="demo",
    provider="demo",
    api="openai-completions",
    base_url="https://demo",
    context_window=1000,
    max_tokens=100,
    reasoning=False,
    cost=Cost(),
    compat=CompatFlags(),
)


def _turn(text: str = "") -> AssistantMessage:
    """A fresh snapshot per turn: the loop appends it to the state as-is."""
    return AssistantMessage(
        content=[TextContent(text=text)] if text else [],
        provider="demo",
        model="demo",
        usage=Usage(),
        stop_reason="pending",
        timestamp=0,
    )


def offline_stream(requests: list[list[Message]]):
    """A fake StreamFn: turn 1 requests the tool, turn 2 answers with its result."""

    async def fn(model, messages, tools, options):
        requests.append(messages)
        if len(requests) == 1:
            partial = _turn()
            yield AssistantEvent(type="start", partial=partial)
            call = ToolCall(id="call_1", name="add", arguments={"a": 2, "b": 3})
            partial.content.append(call)
            yield AssistantEvent(type="toolcall_start", partial=partial, content_index=0)
            yield AssistantEvent(
                type="toolcall_end", partial=partial, content_index=0, tool_call=call
            )
            partial.stop_reason = "tool_use"
            yield AssistantEvent(type="done", partial=partial, reason="tool_use")
            return

        partial = _turn("")
        partial.content.append(TextContent(text=""))
        yield AssistantEvent(type="start", partial=partial)
        for chunk in ("2 + 3 = ", "5"):
            partial.content[0].text += chunk
            yield AssistantEvent(type="text_delta", partial=partial, content_index=0, delta=chunk)
        partial.stop_reason = "stop"
        yield AssistantEvent(type="done", partial=partial, reason="stop")

    return fn


def _trace(events: list[str]) -> str:
    """Collapse runs: a turn emits four message_update deltas in a row, not four facts."""
    parts: list[str] = []
    for event in events:
        if parts and parts[-1].split(" ×")[0] == event:
            count = int(parts[-1].split(" ×")[1]) + 1 if " ×" in parts[-1] else 2
            parts[-1] = f"{event} ×{count}"
        else:
            parts.append(event)
    return ", ".join(parts)


def _text(message: Message) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    return "".join(
        block.text for block in content if isinstance(block, TextContent)
    )


async def main() -> int:
    # 1) execute is a plain coroutine -- call it directly if you want.
    direct = await ADD.execute("call_1", {"a": 2, "b": 3}, None)
    print("direct call:", _text(direct))

    # 2) the same tool, this time invoked by the agent loop.
    requests: list[list[Message]] = []
    agent = Agent(
        model=MODEL,
        system_prompt="demo",
        tools=[ADD],
        api_key="demo",
        stream_fn=offline_stream(requests),
    )
    events: list[str] = []
    agent.subscribe(lambda event: events.append(event.type))
    await agent.prompt("what is 2 + 3?")

    results = [m for m in agent.messages if isinstance(m, ToolResultMessage)]
    answer = _text(agent.messages[-1])
    print("events:", _trace(events))
    print("tool ran:", results[0].content[0].text if results else "(never ran)")
    print("second request saw:", ", ".join(type(m).__name__ for m in requests[-1]))
    print("answer:", answer)

    ok = (
        answer == "2 + 3 = 5"
        and len(results) == 1
        and results[0].content[0].text == "5"
        and results[0].is_error is False
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
