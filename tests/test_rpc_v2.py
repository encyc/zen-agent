"""RPC protocol v2: runtime control, approvals, sessions and commands."""

import asyncio
import json
import time
from pathlib import Path

import pytest

from zenagent.app.chat import ChatController
from zenagent.app.commands import AppServices, build_default_commands
from zenagent.app.models import ModelManager
from zenagent.app.sessions import SessionManager
from zenagent.cli.rpc import RpcServer, serialize_event
from zenagent.events import AgentEvent, AssistantEvent
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


async def test_snapshot_verbs_match_the_in_process_catalogs(runtime_factory):
    """Embedders get the picker/command catalogs and transcript without a replay."""
    _runtime, server, writes, queue = await build(runtime_factory)
    await run_lines(
        server,
        queue,
        [
            {"type": "commands", "id": "c"},
            {"type": "models", "id": "m"},
            {"type": "transcript", "id": "t"},
        ],
    )
    results = {message["id"]: message for message in decode(writes)}
    commands = {entry["name"]: entry["description"] for entry in results["c"]["commands"]}
    assert {"help", "tools"} <= commands.keys()
    assert all(commands.values())
    assert results["m"]["models"] and "id" in results["m"]["models"][0]
    assert results["t"]["transcript"] == []
    assert results["t"]["notices"] == []


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
            "--no-skills",
            "--no-templates",
            "--no-auto-compact",
            "--extension", str(tmp_path / "ext.py"),
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
    assert options.skills is False
    assert options.templates is False
    assert options.auto_compact is False
    assert [str(path) for path in options.extension_paths] == [
        str(tmp_path / "ext.py")
    ]


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
    runtime, server, writes, _queue = await build(
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
    runtime, server, writes, _queue = await build(
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


def test_rpc_cli_starts_without_an_explicit_mode(monkeypatch):
    """`zen rpc` (the documented invocation) must build a gate, not crash."""
    from zenagent.app.runtime import Runtime
    from zenagent.cli import rpc as rpc_module

    captured: dict = {}

    async def fake_bootstrap(cls, options, **kwargs):
        captured.update(options=options, **kwargs)
        raise RuntimeError("stop here")

    monkeypatch.setattr(Runtime, "bootstrap", classmethod(fake_bootstrap))

    code = rpc_module.main([])

    assert code == 2
    assert captured["options"].mode == "ask"
    assert captured["assume_yes"] is False
    assert captured["gate"].mode == "ask"


def test_rpc_cli_yes_resolves_to_zen_and_builds_the_guard_policy(
    monkeypatch, tmp_path
):
    """`--yes` maps to zen, and the guard flags reach the gate's policy."""
    from zenagent.app.runtime import Runtime
    from zenagent.cli import rpc as rpc_module

    captured: dict = {}

    async def fake_bootstrap(cls, options, **kwargs):
        captured.update(options=options, **kwargs)
        raise RuntimeError("stop here")

    monkeypatch.setattr(Runtime, "bootstrap", classmethod(fake_bootstrap))
    outside = tmp_path / "outside"
    outside.mkdir()

    code = rpc_module.main(
        [
            "--yes",
            "--allow-outside",
            str(outside),
            "--allow-destructive",
            "--allow-protected",
        ]
    )

    assert code == 2
    assert captured["options"].mode == "zen"
    assert captured["assume_yes"] is True
    gate = captured["gate"]
    assert gate.mode == "zen"
    policy = gate._gate.policy  # the broker wraps the real gate
    assert str(outside.resolve()) in [str(path) for path in policy.allow_outside]
    assert policy.allow_destructive is True
    assert policy.allow_protected is True


def test_rpc_cli_rejects_an_unknown_mode_without_a_traceback(monkeypatch):
    from zenagent.cli import rpc as rpc_module

    with pytest.raises(SystemExit) as excinfo:
        rpc_module.main(["--mode", "bogus"])
    assert excinfo.value.code == 2


def test_rpc_cli_serves_the_documented_smoke_client(monkeypatch, app_env):
    """The one-liner from docs/guide/cli.md: ``printf ... | zen rpc``."""
    from zenagent.cli import rpc as rpc_module

    requests = [
        '{"type":"status","id":"1"}',
        '{"type":"shutdown","id":"2"}',
    ]
    outputs: list[dict] = []

    async def fake_read_line():
        if requests:
            return requests.pop(0)
        return None

    def fake_write_line(text: str) -> None:
        outputs.append(json.loads(text))

    monkeypatch.setattr(rpc_module, "_stdin_line", fake_read_line)
    monkeypatch.setattr(rpc_module, "_stdout_line", fake_write_line)

    assert rpc_module.main(["--no-mcp", "--no-skills"]) == 0
    by_id = {message.get("id"): message for message in outputs}
    assert by_id["1"]["type"] == "result"
    assert by_id["1"]["mode"] == "ask"
    assert by_id["2"]["ok"] is True


def test_serialize_event_keeps_the_final_message_and_tool_details():
    """The projection must carry data the TUI/REPL consume (P1 event loss)."""
    from zenagent.cli.rpc import serialize_event
    from zenagent.events import AssistantEvent
    from zenagent.types import AssistantMessage, TextContent, Usage

    message = AssistantMessage(
        content=[TextContent(text="final answer")],
        provider="p",
        model="m",
        usage=Usage(),
        stop_reason="stop",
        timestamp=1,
    )
    payload = serialize_event(AgentEvent(type="message_end", message=message))
    assert payload["message"]["role"] == "assistant"
    assert payload["message"]["text"] == "final answer"
    assert payload["message"]["stop_reason"] == "stop"

    from types import SimpleNamespace

    result = SimpleNamespace(
        content=[TextContent(text="ok")],
        details={"diff": "-a\n+b", "exit_code": 0},
    )
    payload = serialize_event(
        AgentEvent(type="tool_execution_end", tool_name="edit_file", result=result)
    )
    assert json.loads(payload["details"]) == {"diff": "-a\n+b", "exit_code": 0}

    failed = AssistantMessage(
        content=[],
        provider="p",
        model="m",
        usage=Usage(),
        stop_reason="error",
        timestamp=1,
        error_message="provider exploded",
    )
    payload = serialize_event(
        AgentEvent(
            type="message_update",
            assistant_event=AssistantEvent(type="error", partial=failed, reason="error"),
        )
    )
    assert payload["assistant_event"]["error"] == "provider exploded"


async def test_prompt_failure_is_reported_with_its_error_text(runtime_factory):
    from zenagent.events import AssistantEvent
    from zenagent.types import AssistantMessage, TextContent, Usage

    async def failing_stream(model, messages, tools, options):
        partial = AssistantMessage(
            content=[TextContent(text="")],
            provider="p",
            model="m",
            usage=Usage(),
            stop_reason="error",
            timestamp=0,
            error_message="provider exploded",
        )
        yield AssistantEvent(type="start", partial=partial)
        yield AssistantEvent(type="error", partial=partial, reason="error")

    _runtime, server, writes, _queue = await build(
        runtime_factory, stream_fn=failing_stream
    )
    await server._handle_line(json.dumps({"type": "prompt", "id": "p", "text": "hi"}))
    await server._task

    payload = next(message for message in decode(writes) if message.get("id") == "p")
    assert payload["ok"] is False
    assert payload["termination"] == "error"
    assert "provider exploded" in payload["error"]


async def test_prompt_text_falls_back_to_the_final_message(runtime_factory):
    """Providers that skip text_delta still yield the canonical answer."""
    from zenagent.events import AssistantEvent
    from zenagent.types import AssistantMessage, TextContent, Usage

    async def end_only_stream(model, messages, tools, options):
        partial = AssistantMessage(
            content=[TextContent(text="canonical answer")],
            provider="p",
            model="m",
            usage=Usage(),
            stop_reason="stop",
            timestamp=0,
        )
        yield AssistantEvent(type="start", partial=partial)
        yield AssistantEvent(type="done", partial=partial, reason="stop")

    _runtime, server, writes, _queue = await build(
        runtime_factory, stream_fn=end_only_stream
    )
    await server._handle_line(json.dumps({"type": "prompt", "id": "p", "text": "hi"}))
    await server._task

    payload = next(message for message in decode(writes) if message.get("id") == "p")
    assert payload["ok"] is True
    assert payload["text"] == "canonical answer"


async def test_command_result_forwards_the_screen_target(runtime_factory):
    """Screen commands carry a payload (memory-edit's entry id) to the client."""
    from zenagent.app.commands import CommandResult

    _runtime, server, writes, _queue = await build(runtime_factory)

    class StubCommands:
        def get(self, name):
            return object()

        async def dispatch(self, name, argument):
            return CommandResult(screen="memory-edit", target="2431")

    server.commands = StubCommands()
    await server._handle_line(
        json.dumps(
            {"type": "command", "id": "c", "name": "memory", "argument": "edit 2431"}
        )
    )

    payload = next(message for message in decode(writes) if message.get("id") == "c")
    assert payload["screen"] == "memory-edit"
    assert payload["target"] == "2431"


async def test_status_reports_context_thinking_and_work_state(runtime_factory):
    _runtime, server, writes, _queue = await build(runtime_factory)
    await server._handle_line(json.dumps({"type": "status", "id": "s"}))

    payload = decode(writes)[0]
    assert payload["ok"] is True
    assert payload["thinking"] is not None
    assert payload["context_window"] > 0
    assert payload["context_used"] == 0
    assert payload["todos"] == []
    assert payload["follow_ups"] == 0
    assert payload["subagents"] == []
    assert payload["jobs"] == []
    assert payload["pending"] == []


def _png(tmp_path):
    path = tmp_path / "shot.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
    return path


async def test_prompt_accepts_image_paths(runtime_factory, tmp_path):
    from zenagent.types import ImageContent, UserMessage

    _runtime, server, writes, _queue = await build(runtime_factory)
    await server._handle_line(
        json.dumps(
            {
                "type": "prompt",
                "id": "p",
                "text": "look at this",
                "images": [str(_png(tmp_path))],
            }
        )
    )
    await server._task

    user = next(m for m in server.agent.messages if isinstance(m, UserMessage))
    assert isinstance(user.content, list)
    assert any(isinstance(block, ImageContent) for block in user.content)
    payload = next(message for message in decode(writes) if message.get("id") == "p")
    assert payload["ok"] is True


async def test_prompt_rejects_an_image_it_cannot_read(runtime_factory, tmp_path):
    bad = tmp_path / "not-an-image.txt"
    bad.write_text("nope", encoding="utf-8")

    _runtime, server, writes, _queue = await build(runtime_factory)
    await server._handle_line(
        json.dumps(
            {"type": "prompt", "id": "p", "text": "look", "images": [str(bad)]}
        )
    )

    payload = next(message for message in decode(writes) if message.get("id") == "p")
    assert payload["type"] == "error"
    assert "does not look like" in payload["message"]


async def test_deleting_the_current_session_resets_the_controller(runtime_factory):
    from zenagent.types import UserMessage

    runtime, server, writes, _queue = await build(runtime_factory)
    runtime.session.append(UserMessage(content="hello", timestamp=1))
    server.agent.replace_messages([UserMessage(content="hello", timestamp=1)])
    server.chat.reload_transcript()
    assert server.chat.transcript

    await server._handle_line(
        json.dumps(
            {
                "type": "session",
                "id": "d",
                "op": "delete",
                "path": str(runtime.session.path),
            }
        )
    )

    payload = next(message for message in decode(writes) if message.get("id") == "d")
    assert payload["ok"] is True
    assert server.agent.messages == []
    assert server.chat.transcript == []


async def test_shutdown_denies_pending_approvals(runtime_factory):
    """EOF with an unanswered approval_request must not stall for the timeout."""
    _runtime, server, _writes, _queue = await build(runtime_factory)
    future = asyncio.get_running_loop().create_future()
    server._approvals["a0"] = future

    await server._shutdown()

    assert future.done() is True
    assert future.result() == "deny"


def test_serialize_event_carries_user_text_and_steering():
    plain = serialize_event(
        AgentEvent(
            type="message_start",
            message=UserMessage(content="hello", timestamp=1),
        )
    )
    assert plain["message"] == {"role": "user", "text": "hello"}

    steered = serialize_event(
        AgentEvent(
            type="message_start",
            message=UserMessage(content="nudge", timestamp=1, steering=True),
        )
    )
    assert steered["message"]["text"] == "nudge"
    assert steered["message"]["steering"] is True


async def test_transcript_exposes_one_compaction_across_refetches(runtime_factory):
    runtime, server, writes, queue = await build(runtime_factory)
    runtime.agent.replace_messages(
        [
            UserMessage(content="long chat", timestamp=1),
            AssistantMessage(
                content=[TextContent(text="done")],
                provider="p",
                model="m",
                usage=Usage(input=500),
                stop_reason="stop",
                timestamp=2,
            ),
        ]
    )
    await server.chat.compact_now()

    await run_lines(
        server,
        queue,
        [
            {"type": "transcript", "id": "t1"},
            {"type": "transcript", "id": "t2"},
        ],
    )
    payloads = {message["id"]: message for message in decode(writes)}

    assert [record["type"] for record in payloads["t1"]["compactions"]] == [
        "compaction"
    ]
    assert payloads["t1"]["notices"] == []
    assert payloads["t2"]["compactions"] == payloads["t1"]["compactions"]
    assert payloads["t1"]["transcript"][0]["id"] == "v1"


async def test_transcript_views_carry_ids_and_assistant_metadata(runtime_factory):
    runtime, server, writes, queue = await build(runtime_factory)
    runtime.agent.replace_messages(
        [
            AssistantMessage(
                content=[TextContent(text="hi")],
                provider="p",
                model="deepseek-chat",
                usage=Usage(input=3, output=2),
                stop_reason="stop",
                timestamp=7,
                duration_ms=42,
            )
        ]
    )
    server.chat.reload_transcript()

    await run_lines(server, queue, [{"type": "transcript", "id": "t"}])

    view = decode(writes)[0]["transcript"][0]
    assert view["id"] == "v1"
    assert view["model"] == "deepseek-chat"
    assert view["duration_ms"] == 42
    assert view["timestamp"] == 7


async def test_session_list_carries_created_updated_model_and_lineage(runtime_factory):
    runtime, server, writes, queue = await build(runtime_factory)
    runtime.session.append(UserMessage(content="hello", timestamp=1))
    runtime.session.append(
        AssistantMessage(
            content=[TextContent(text="hi")],
            provider="p",
            model="deepseek-chat",
            usage=Usage(),
            stop_reason="stop",
            timestamp=2,
        )
    )

    await run_lines(server, queue, [{"type": "session", "id": "l", "op": "list"}])

    entry = decode(writes)[0]["sessions"][0]
    assert entry["created_at"] > 0
    assert entry["updated_at"] == Path(entry["path"]).stat().st_mtime
    assert entry["model"] == "deepseek-chat"
    assert entry["derived_from"] == ""
