"""agent_end flush resilience: a failed write is deferred, never dropped.

The flush goes through ``runtime._flush_unpersisted`` — a write that fails
mid-drain keeps its tail pending (the old inline handler silently dropped
the failed message and everything after it). The pending tail lands at the
next ``agent_end`` (order preserved) or at ``close``, whichever comes first.
(The dsh-style per-message checkpoint — assistant durable before its tool
bodies run — needs a loop-level pre-side-effect hook; flushing inside the
event subscriber deadlocks the takeover path, so it stays a documented
future design.)
"""

from conftest import tool_then_text_stream

from zenagent.agent.session import Session
from zenagent.types import AssistantMessage, ToolResultMessage, UserMessage


def make_flaky_append(monkeypatch):
    """Patch Session.append so exactly the first assistant write fails."""
    real_append = Session.append
    failed = {"assistant": False}

    async def flaky_append(self, message):
        if not failed["assistant"] and isinstance(message, AssistantMessage):
            failed["assistant"] = True
            raise OSError(28, "disk full")
        return await real_append(self, message)

    monkeypatch.setattr(Session, "append", flaky_append)


async def test_a_failed_flush_is_deferred_to_close(runtime_factory, monkeypatch):
    runtime = await runtime_factory(stream_fn=tool_then_text_stream())
    make_flaky_append(monkeypatch)

    await runtime.agent.prompt("read the readme")

    runtime.session.ensure_loaded()
    kinds = [type(m) for m in runtime.session.messages]
    assert kinds == [UserMessage], "the failed tail is deferred, not written yet"

    await runtime.close()

    runtime.session.ensure_loaded()
    kinds = [type(m) for m in runtime.session.messages]
    assert kinds == [UserMessage, AssistantMessage, ToolResultMessage, AssistantMessage]
    assert runtime.session.messages[0].content == "read the readme"


async def test_a_failed_flush_lands_on_the_next_run(runtime_factory, monkeypatch):
    runtime = await runtime_factory(stream_fn=tool_then_text_stream())
    make_flaky_append(monkeypatch)

    await runtime.agent.prompt("read the readme")

    # A clean second run: the deferred tail flushes before its own messages.
    monkeypatch.undo()
    await runtime.agent.prompt("and again")

    runtime.session.ensure_loaded()
    kinds = [type(m) for m in runtime.session.messages]
    assert kinds == [
        UserMessage,
        AssistantMessage,
        ToolResultMessage,
        AssistantMessage,
        UserMessage,
        AssistantMessage,
        ToolResultMessage,
        AssistantMessage,
    ]
    assert runtime.session.messages[4].content == "and again", (
        "the deferred tail must land before the new run's messages"
    )
