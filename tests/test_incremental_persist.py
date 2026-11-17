"""Fail-closed incremental persistence: each message lands at its own end.

The dsh checkpoints this buys: the assistant turn is durable before its tool
bodies run (``message_end`` precedes ``tool_execution_start``), and a tool
result is durable before the next model request. ``agent_end`` stays as the
backstop for aborts and provider errors, which skip ``message_end``.
"""

from conftest import tool_then_text_stream

from zenagent.agent.session import Session
from zenagent.types import AssistantMessage, ToolResultMessage


async def test_assistant_message_durable_before_tool_executes(
    runtime_factory, monkeypatch
):
    import zenagent.agent.loop as loop_module
    from zenagent.agent.tool_exec import run_tool_batch as real_batch

    runtime = await runtime_factory(stream_fn=tool_then_text_stream())
    seen_at_batch_start: list[list[str]] = []

    async def spy_batch(calls, config, abort, emit):
        path = runtime.session.path
        lines = (
            path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        )
        seen_at_batch_start.append(lines)
        return await real_batch(calls, config, abort, emit)

    monkeypatch.setattr(loop_module, "run_tool_batch", spy_batch)

    await runtime.agent.prompt("read the readme")

    # The tool batch saw the file BEFORE executing: the assistant turn with
    # its tool call was already on disk (the run had never reached agent_end).
    assert seen_at_batch_start, "the tool batch must run"
    batch_lines = seen_at_batch_start[0]
    assert any('"toolCall"' in line for line in batch_lines), (
        "the assistant turn must be durable before tool bodies run"
    )


async def test_message_end_flush_failure_keeps_agent_end_backstop(
    runtime_factory, monkeypatch
):
    runtime = await runtime_factory(stream_fn=tool_then_text_stream())
    real_append = Session.append
    failed = {"assistant": False}

    async def flaky_append(self, message):
        if not failed["assistant"] and isinstance(message, AssistantMessage):
            failed["assistant"] = True
            raise OSError(28, "disk full")
        return await real_append(self, message)

    monkeypatch.setattr(Session, "append", flaky_append)

    await runtime.agent.prompt("read the readme")

    runtime.session.ensure_loaded()
    kinds = [type(m) for m in runtime.session.messages]
    assert AssistantMessage in kinds
    assert ToolResultMessage in kinds
    # Every message landed, including the one whose flush failed mid-run.
    assert len(runtime.session.messages) == 4  # user, tool-call, result, text
