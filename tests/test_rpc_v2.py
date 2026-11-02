"""RPC protocol v2: runtime control, approvals, sessions and commands."""

import asyncio
import json
import time

from zenagent.app.chat import ChatController
from zenagent.app.commands import AppServices, build_default_commands
from zenagent.app.models import ModelManager
from zenagent.app.sessions import SessionManager
from zenagent.cli.rpc import RpcServer
from zenagent.events import AssistantEvent
from zenagent.types import (
    AssistantMessage,
    TextContent,
    Usage,
    UserMessage,
)


def text_stream(text: str = "ok"):
    async def stream(model, messages, tools, options):
        partial = AssistantMessage(
            content=[],
            provider="p",
            model="m",
            usage=Usage(),
            stop_reason="pending",
            timestamp=int(time.time()),
        )
        yield AssistantEvent(type="start", partial=partial)
        block = TextContent(text="")
        partial.content.append(block)
        for chunk in (text, ""):
            block.text += chunk
            if chunk:
                yield AssistantEvent(
                    type="text_delta",
                    partial=partial,
                    content_index=0,
                    delta=chunk,
                )
        partial.stop_reason = "stop"
        yield AssistantEvent(type="done", partial=partial, reason="stop")

    return stream


async def build(runtime_factory, *, stream_fn=None, **overrides):
    runtime = await runtime_factory(
        stream_fn=stream_fn or text_stream(), **overrides
    )
    chat = ChatController(runtime)
    services = AppServices(
        runtime=runtime,
        chat=chat,
        models=ModelManager(runtime),
        sessions=SessionManager(runtime),
    )
    writes: list[str] = []
    queue: list[str] = []

    async def read_line() -> str | None:
        await asyncio.sleep(0)
        return queue.pop(0) if queue else None

    server = RpcServer(
        runtime=runtime,
        read_line=read_line,
        write_line=writes.append,
        chat=chat,
        models=services.models,
        sessions=services.sessions,
        commands=build_default_commands(services),
    )
    return runtime, server, writes, queue


def decode(writes: list[str]) -> list[dict]:
    return [json.loads(line) for line in writes]


async def run_lines(server: RpcServer, queue: list[str], lines: list[dict]) -> None:
    queue.extend(json.dumps(line) for line in lines)
    await server.run()


async def test_status_reports_session_mode_tools_and_busy(runtime_factory):
    _runtime, server, writes, queue = await build(runtime_factory)
    await run_lines(server, queue, [{"type": "status", "id": "s"}])
    payload = decode(writes)[0]
    assert payload["ok"] is True
    assert payload["session"].endswith(".jsonl")
    assert payload["mode"] == "ask"
    assert payload["tools"] > 0
    assert payload["busy"] is False


async def test_tools_request_lists_model_visible_tools(runtime_factory):
    _runtime, server, writes, queue = await build(runtime_factory)
    await run_lines(server, queue, [{"type": "tools", "id": "t"}])
    tools = {tool["name"] for tool in decode(writes)[0]["tools"]}
    assert {"read_file", "bash", "todo", "task"} <= tools


async def test_mode_and_model_requests_switch_the_runtime(runtime_factory, app_env):
    _runtime, server, writes, queue = await build(runtime_factory)
    await run_lines(
        server,
        queue,
        [
            {"type": "mode", "id": "m1", "mode": "plan"},
            {"type": "mode", "id": "m2", "mode": "yolo"},
            {"type": "model", "id": "m3", "model_id": "deepseek-flash"},
            {"type": "model", "id": "m4", "model_id": "nope"},
        ],
    )
    results = {message["id"]: message for message in decode(writes)}
    assert results["m1"]["ok"] is True and results["m1"]["mode"] == "plan"
    assert results["m2"]["ok"] is False
    assert results["m3"]["ok"] is True
    assert results["m4"]["ok"] is False


async def test_steer_while_idle_queues_a_follow_up(runtime_factory):
    _runtime, server, writes, queue = await build(runtime_factory)
    await run_lines(
        server,
        queue,
        [
            {"type": "steer", "id": "s1", "text": "also check logs"},
            {"type": "status", "id": "s2"},
        ],
    )
    messages = decode(writes)
    assert messages[0]["ok"] is True
    assert messages[1]["follow_ups"] == 1


async def test_approval_round_trip_allow_always_and_deny(runtime_factory):
    _runtime, server, writes, _queue = await build(runtime_factory)

    for decision in ("allow", "always", "deny"):
        task = asyncio.create_task(server.confirm("write_file", {"path": "x"}))
        while not any(
            "approval_request" in message for message in writes
        ):
            await asyncio.sleep(0.01)
        request_id = next(
            json.loads(message)["event"]["request_id"]
            for message in writes
            if "approval_request" in message
        )
        assert await server._handle_line(
            json.dumps(
                {
                    "type": "approval",
                    "id": f"a-{decision}",
                    "request_id": request_id,
                    "decision": decision,
                }
            )
        )
        assert await task == decision
        writes.clear()


async def test_approval_times_out_to_deny(runtime_factory):
    _runtime, server, _writes, _queue = await build(runtime_factory)
    server.approval_timeout_s = 0.01
    assert await server.confirm("bash", {"command": "ls"}) == "deny"


async def test_approval_for_an_unknown_request_is_an_error(runtime_factory):
    _runtime, server, writes, _queue = await build(runtime_factory)
    await server._handle_line(
        json.dumps(
            {
                "type": "approval",
                "id": "x",
                "request_id": "missing",
                "decision": "allow",
            }
        )
    )
    assert decode(writes)[0]["type"] == "error"


async def test_session_current_list_and_new(runtime_factory):
    runtime, server, writes, queue = await build(runtime_factory)
    await run_lines(
        server,
        queue,
        [
            {"type": "session", "id": "s1", "op": "current"},
            {"type": "session", "id": "s2", "op": "list"},
            {"type": "session", "id": "s3", "op": "new"},
        ],
    )
    messages = {message["id"]: message for message in decode(writes)}
    assert messages["s1"]["path"].endswith(".jsonl")
    assert messages["s2"]["sessions"]
    assert messages["s3"]["path"] != messages["s1"]["path"]
    assert str(runtime.session.path) == messages["s3"]["path"]


async def test_session_compact_switches_the_file(runtime_factory):
    runtime, server, writes, queue = await build(runtime_factory)
    runtime.agent.replace_messages([UserMessage(content="hi", timestamp=1)])
    await run_lines(
        server, queue, [{"type": "session", "id": "c", "op": "compact"}]
    )
    payload = decode(writes)[0]
    assert payload["compacted"] is True
    assert payload["path"].endswith("-compact.jsonl")


async def test_session_undo_and_export(runtime_factory):
    runtime, server, writes, queue = await build(runtime_factory)
    runtime.agent.replace_messages(
        [
            UserMessage(content="hi", timestamp=1),
            AssistantMessage(
                content=[TextContent(text="done")],
                provider="p",
                model="m",
                usage=Usage(),
                stop_reason="stop",
                timestamp=2,
            ),
        ]
    )
    await run_lines(
        server,
        queue,
        [
            {"type": "session", "id": "u", "op": "undo"},
            {"type": "session", "id": "e", "op": "export"},
        ],
    )
    messages = {message["id"]: message for message in decode(writes)}
    assert messages["u"]["dropped"] == 2
    assert messages["e"]["path"].endswith(".md")


async def test_session_retry_reruns_the_last_prompt(runtime_factory):
    runtime, server, writes, queue = await build(runtime_factory)
    runtime.agent.replace_messages(
        [
            UserMessage(content="hello", timestamp=1),
            AssistantMessage(
                content=[TextContent(text="hi")],
                provider="p",
                model="m",
                usage=Usage(),
                stop_reason="stop",
                timestamp=2,
            ),
        ]
    )
    await run_lines(server, queue, [{"type": "session", "id": "r", "op": "retry"}])
    await server.chat.wait_for_idle()
    payload = decode(writes)[0]
    assert payload["retried"] is True
    assert payload["text"] == "hello"


async def test_command_request_dispatches_slash_commands(runtime_factory):
    runtime, server, writes, queue = await build(runtime_factory)
    await run_lines(
        server,
        queue,
        [
            {"type": "command", "id": "c1", "name": "tools"},
            {"type": "command", "id": "c2", "name": "mode", "argument": "plan"},
            {"type": "command", "id": "c4", "name": "nope"},
            {"type": "command", "id": "c3", "name": "quit"},
            {"type": "status", "id": "never"},
        ],
    )
    messages = {message["id"]: message for message in decode(writes)}
    assert "read_file" in messages["c1"]["message"]
    assert runtime.mode == "plan"
    assert messages["c4"]["type"] == "error"
    assert messages["c3"]["quit"] is True
    # `/quit` shuts the server down: the request after it is never read.
    assert "never" not in messages


async def test_prompt_runs_through_the_chat_controller(runtime_factory):
    _runtime, server, writes, _queue = await build(runtime_factory)
    await server._handle_line(
        json.dumps({"type": "prompt", "id": "p1", "text": "hi"})
    )
    await server._task
    payload = next(message for message in decode(writes) if message.get("id") == "p1")
    assert payload["ok"] is True
    assert payload["text"] == "ok"
    assert payload["session"].endswith(".jsonl")


def test_rpc_flags_forward_to_runtime_options(monkeypatch, tmp_path):
    from zenagent.app.runtime import Runtime
    from zenagent.cli import rpc as rpc_module

    captured: dict = {}

    async def fake_bootstrap(cls, options, **kwargs):
        captured["options"] = options
        raise RuntimeError("stop here")

    monkeypatch.setattr(Runtime, "bootstrap", classmethod(fake_bootstrap))
    trace = tmp_path / "t.jsonl"
    metrics = tmp_path / "m.jsonl"

    code = rpc_module.main(
        [
            "--thinking", "low",
            "--mode", "plan",
            "--trace", str(trace),
            "--metrics", str(metrics),
            "--retries", "2",
            "--no-mcp",
        ]
    )

    assert code == 2
    options = captured["options"]
    assert options.thinking == "low"
    assert options.mode == "plan"
    assert str(options.trace) == str(trace)
    assert str(options.metrics) == str(metrics)
    assert options.retries == 2
    assert options.mcp is False


async def slow_stream(delay: float = 0.2, text: str = "slow"):
    async def stream(model, messages, tools, options):
        partial = AssistantMessage(
            content=[],
            provider="p",
            model="m",
            usage=Usage(),
            stop_reason="pending",
            timestamp=int(time.time()),
        )
        yield AssistantEvent(type="start", partial=partial)
        await asyncio.sleep(delay)
        block = TextContent(text=text)
        partial.content.append(block)
        yield AssistantEvent(
            type="text_delta", partial=partial, content_index=0, delta=text
        )
        partial.stop_reason = "stop"
        yield AssistantEvent(type="done", partial=partial, reason="stop")

    return stream


async def test_cancel_aborts_a_run_started_by_a_session_retry(runtime_factory):
    runtime, server, writes, queue = await build(
        runtime_factory, stream_fn=await slow_stream()
    )
    runtime.agent.replace_messages(
        [
            UserMessage(content="hello", timestamp=1),
            AssistantMessage(
                content=[TextContent(text="hi")],
                provider="p",
                model="m",
                usage=Usage(),
                stop_reason="stop",
                timestamp=2,
            ),
        ]
    )
    await server._handle_line(
        json.dumps({"type": "session", "id": "r", "op": "retry"})
    )
    await asyncio.sleep(0.05)
    assert server.busy is True
    await server._handle_line(json.dumps({"type": "cancel", "id": "c"}))
    payload = next(message for message in decode(writes) if message.get("id") == "c")
    assert payload["cancelled"] is True
    await server.chat.wait_for_idle()
    assert server.agent.is_streaming is False


async def test_mutating_session_ops_are_rejected_while_a_run_is_active(
    runtime_factory,
):
    runtime, server, writes, queue = await build(
        runtime_factory, stream_fn=await slow_stream()
    )
    runtime.agent.replace_messages([UserMessage(content="hello", timestamp=1)])
    await server._handle_line(
        json.dumps({"type": "prompt", "id": "p", "text": "hi"})
    )
    await asyncio.sleep(0.05)
    await server._handle_line(
        json.dumps({"type": "session", "id": "u", "op": "undo"})
    )
    payload = next(message for message in decode(writes) if message.get("id") == "u")
    assert payload["type"] == "error"
    assert "busy" in payload["message"]
    server.agent.abort()
    await asyncio.gather(server._task, return_exceptions=True)
