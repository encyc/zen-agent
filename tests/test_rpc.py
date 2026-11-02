"""JSONL RPC server: protocol, results, error handling, shutdown."""

import asyncio
import json

from zenagent.agent import Agent
from zenagent.cli.rpc import RpcServer
from zenagent.events import AssistantEvent
from zenagent.providers.catalog import Model
from zenagent.providers.compat import CompatFlags
from zenagent.types import AssistantMessage, Cost, TextContent, Usage


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


def test_serialize_event_carries_bounded_partial_tool_output():
    from zenagent.agent.loop import ToolResultAgent
    from zenagent.cli.rpc import serialize_event
    from zenagent.events import AgentEvent

    partial = ToolResultAgent(content=[TextContent(text="x" * 10_000)])
    payload = serialize_event(
        AgentEvent(
            type="tool_execution_update", tool_name="bash", partial_result=partial
        )
    )
    assert payload["type"] == "tool_execution_update"
    assert payload["tool_name"] == "bash"
    assert len(payload["partial_result"]) == 4000

    # a plain event stays unchanged: no stray key
    assert "partial_result" not in serialize_event(AgentEvent(type="turn_start"))


async def test_rpc_wires_the_task_budget_from_config(monkeypatch):
    import argparse
    import types

    from zenagent.agent.subagents import SubagentLimits
    from zenagent.cli import rpc as rpc_mod
    from zenagent.config import UserConfig

    captured: dict = {}

    class _FakeServer:
        def __init__(self, agent, read_line, write_line):
            captured["agent"] = agent

        async def run(self) -> int:
            return 0

    monkeypatch.setattr(
        "zenagent.config.load_user_config",
        lambda: UserConfig(
            task_max_turns=5,
            task_max_transcript_bytes=4096,
            task_max_transcript_file_bytes=2048,
        ),
    )
    monkeypatch.setattr(
        "zenagent.config.apply_user_config", lambda catalog, config: catalog
    )
    monkeypatch.setattr("zenagent.providers.catalog.load_catalog", dict)
    monkeypatch.setattr(
        "zenagent.cli.main.resolve_model",
        lambda catalog, model_id: types.SimpleNamespace(reasoning=False),
    )
    monkeypatch.setattr("zenagent.cli.main._api_key_for", lambda catalog, model: "k")
    monkeypatch.setattr(
        "zenagent.cli.main._build_agent", lambda *args, **kwargs: "agent"
    )
    monkeypatch.setattr(
        "zenagent.tools.task.build_task_tool",
        lambda **kwargs: captured.setdefault("task_tool", kwargs),
    )
    monkeypatch.setattr("zenagent.cli.rpc.RpcServer", _FakeServer)

    args = argparse.Namespace(model=None, yes=False, max_cost=None, max_turns=None)
    assert await rpc_mod._run(args) == 0

    tool = captured["task_tool"]
    assert tool["max_turns"] == 5
    assert tool["limits"] == SubagentLimits(
        keep_transcripts=64, max_total_bytes=4096, max_file_bytes=2048
    )
