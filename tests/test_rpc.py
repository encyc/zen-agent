"""JSONL RPC server: protocol, results, error handling, shutdown."""

import asyncio
import json

from pyagent.agent import Agent
from pyagent.cli.rpc import RpcServer
from pyagent.events import AssistantEvent
from pyagent.providers.catalog import Model
from pyagent.providers.compat import CompatFlags
from pyagent.types import AssistantMessage, Cost, TextContent, Usage


def model() -> Model:
    return Model(
        id="m", provider="p", api="openai-completions", base_url="https://x/v1",
        context_window=1000, max_tokens=100, reasoning=False, cost=Cost(),
        compat=CompatFlags(),
    )


def stream(text="hello"):
    async def fn(model_, messages, tools, options):
        partial = AssistantMessage(
            content=[], provider="p", model="m", usage=Usage(),
            stop_reason="pending", timestamp=0,
        )
        yield AssistantEvent(type="start", partial=partial)
        block = TextContent(text="")
        partial.content.append(block)
        for chunk in (text, " world"):
            block.text += chunk
            yield AssistantEvent(type="text_delta", partial=partial, content_index=0, delta=chunk)
        partial.stop_reason = "stop"
        yield AssistantEvent(type="done", partial=partial, reason="stop")

    return fn


def make_agent(text="hello") -> Agent:
    return Agent(model=model(), system_prompt="s", api_key="k", stream_fn=stream(text))


def drive(lines: list[str]):
    queue = list(lines)
    writes: list[str] = []

    async def read_line() -> str | None:
        await asyncio.sleep(0)
        return queue.pop(0) if queue else None

    def write_line(text: str) -> None:
        writes.append(text)

    return queue, writes, read_line, write_line


async def run(lines: list[str], agent: Agent | None = None):
    _queue, writes, read_line, write_line = drive(lines)
    server = RpcServer(agent or make_agent(), read_line, write_line)
    code = await server.run()
    return code, [json.loads(line) for line in writes]


async def test_prompt_streams_events_and_returns_a_result():
    code, messages = await run([
        json.dumps({"type": "prompt", "id": "1", "text": "hi"}),
        json.dumps({"type": "shutdown", "id": "9"}),
    ])
    assert code == 0
    types = [message["type"] for message in messages]
    assert "event" in types
    result = next(m for m in messages if m["type"] == "result" and m.get("id") == "1")
    assert result["ok"] is True
    assert result["text"] == "hello world"
    assert result["termination"] == "stop"
    assert result["usage"]["output"] == 0
    # every line is JSON (already parsed) and carries a type
    assert all("type" in message for message in messages)


async def test_status_reports_model_and_message_count():
    _, messages = await run([
        json.dumps({"type": "status", "id": "s"}),
        json.dumps({"type": "shutdown", "id": "9"}),
    ])
    status = next(m for m in messages if m.get("id") == "s")
    assert status["model"] == "m" and status["running"] is False


async def test_bad_json_is_reported_and_the_server_keeps_going():
    _, messages = await run([
        "{not json",
        json.dumps({"type": "status", "id": "s"}),
        json.dumps({"type": "shutdown", "id": "9"}),
    ])
    assert messages[0]["type"] == "error" and "bad request" in messages[0]["message"]
    assert any(m.get("id") == "s" for m in messages)


async def test_unknown_type_and_missing_prompt_text_are_errors():
    _, messages = await run([
        json.dumps({"type": "nope", "id": "x"}),
        json.dumps({"type": "prompt", "id": "y", "text": "  "}),
        json.dumps({"type": "shutdown", "id": "9"}),
    ])
    errors = [m for m in messages if m["type"] == "error"]
    assert any("unknown type" in e["message"] for e in errors)
    assert any("needs text" in e["message"] for e in errors)


async def test_cancel_when_idle_is_a_noop():
    _, messages = await run([
        json.dumps({"type": "cancel", "id": "c"}),
        json.dumps({"type": "shutdown", "id": "9"}),
    ])
    cancelled = next(m for m in messages if m.get("id") == "c")
    assert cancelled["cancelled"] is False


async def test_eof_ends_the_loop():
    code, messages = await run([])
    assert code == 0
    assert messages == []
