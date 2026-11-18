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


async def test_a_failed_flush_never_pollutes_the_next_session(
    runtime_factory, monkeypatch
):
    """The deferred tail belongs to the session it came from.

    A switch must land it in the old file (best effort) — a tail written
    after the switch would append session A's messages into session B's
    file, and a later repair could synthesize a duplicate tool result.
    """
    from zenagent.agent.session import Session, unique_session_path

    runtime = await runtime_factory(stream_fn=tool_then_text_stream())
    path_a = runtime.session.path
    make_flaky_append(monkeypatch)

    await runtime.agent.prompt("read the readme")

    runtime.session.ensure_loaded()
    assert [type(m) for m in runtime.session.messages] == [UserMessage], (
        "the tail is still deferred at switch time"
    )

    monkeypatch.undo()
    await runtime.switch_session(unique_session_path(root=runtime.root))
    path_b = runtime.session.path
    await runtime.agent.prompt("in session B")

    b_messages = Session(path_b).load()
    assert all(m.content != "read the readme" for m in b_messages), (
        "session A's tail must never land in session B's file"
    )
    a_messages = Session(path_a).load()
    assert [type(m) for m in a_messages] == [
        UserMessage,
        AssistantMessage,
        ToolResultMessage,
        AssistantMessage,
    ], "the switch landed the deferred tail in its own session's file"
