"""Define an AgentTool and drive an Agent with a fake stream (fully offline).

    uv run python examples/custom_tool.py
"""

from __future__ import annotations

import asyncio

from zenagent.agent import Agent
from zenagent.agent.loop import AgentTool, ToolResultAgent
from zenagent.events import AssistantEvent
from zenagent.providers.catalog import Model
from zenagent.providers.compat import CompatFlags
from zenagent.types import AssistantMessage, Cost, TextContent, Usage


async def add(call_id, args, abort, on_update=None) -> ToolResultAgent:
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
)


def offline_stream():
    """A fake StreamFn that answers with fixed text (no provider, no network)."""

    async def fn(model, messages, tools, options):
        partial = AssistantMessage(
            content=[TextContent(text="")],
            provider="demo",
            model="demo",
            usage=Usage(),
            stop_reason="pending",
            timestamp=0,
        )
        yield AssistantEvent(type="start", partial=partial)
        for chunk in ("2 + 3 = ", "5"):
            partial.content[0].text += chunk
            yield AssistantEvent(type="text_delta", partial=partial, content_index=0, delta=chunk)
        partial.stop_reason = "stop"
        yield AssistantEvent(type="done", partial=partial, reason="stop")

    return fn


async def main() -> int:
    # 1) call the tool directly
    result = await ADD.execute("call_1", {"a": 2, "b": 3}, None)
    print("tool result:", result.content[0].text)

    # 2) run the agent loop against the fake stream
    model = Model(
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
    agent = Agent(
        model=model,
        system_prompt="demo",
        tools=[ADD],
        api_key="demo",
        stream_fn=offline_stream(),
    )
    events: list[str] = []
    agent.subscribe(lambda event: events.append(event.type))
    await agent.prompt("what is 2 + 3?")
    answer = agent.messages[-1].content[0].text
    print("events:", ", ".join(events))
    print("answer:", answer)
    return 0 if answer == "2 + 3 = 5" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
