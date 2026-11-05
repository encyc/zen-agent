"""Side-channel session records: run boundaries, retries, mode changes.

Unit tests drive :class:`SessionMetaRecorder` with fake events; integration
tests run a real bootstrap against offline streams and reload the session file.
"""

import json
from pathlib import Path

from conftest import text_stream
from zenagent.agent.session import Session
from zenagent.app.session_meta import (
    SessionMetaRecorder,
    make_retry_recorder,
    mode_record,
)
from zenagent.events import AgentEvent, AssistantEvent
from zenagent.types import (
    AssistantMessage,
    TextContent,
    Usage,
    UserMessage,
)


# ---------------------------------------------------------------------------
# Fakes and helpers
# ---------------------------------------------------------------------------


class FakeStore:
    """The append_meta seam: keeps every record in memory, timestamped."""

    def __init__(self) -> None:
        self.records: list[dict] = []

    def append_meta(self, record: dict) -> None:
        import time

        self.records.append({"timestamp": int(time.time()), **record})


def assistant(*, stop="stop", error: str | None = None) -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text="hi")] if stop == "stop" else [],
        provider="p",
        model="m",
        usage=Usage(),
        stop_reason=stop,
        timestamp=1,
        error_message=error,
    )


def records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


# ---------------------------------------------------------------------------
# Unit: SessionMetaRecorder
# ---------------------------------------------------------------------------


def test_recorder_writes_run_start_and_run_end():
    store = FakeStore()
    recorder = SessionMetaRecorder(lambda: store)
    recorder(AgentEvent(type="agent_start"))
    recorder(
        AgentEvent(
            type="agent_end",
            new_messages=[UserMessage(content="hi", timestamp=1), assistant()],
            turns=2,
            termination="stop",
        )
    )
    assert [record["type"] for record in store.records] == ["run_start", "run_end"]
    end = store.records[1]
    assert end["termination"] == "stop"
    assert end["turns"] == 2
    assert isinstance(end["duration_ms"], int)
    assert "detail" not in end


def test_recorder_run_end_carries_terminal_error_detail():
    store = FakeStore()
    recorder = SessionMetaRecorder(lambda: store)
    recorder(AgentEvent(type="agent_start"))
    recorder(
        AgentEvent(
            type="agent_end",
            new_messages=[assistant(stop="error", error="quota exceeded")],
            turns=1,
            termination="error",
        )
    )
    end = store.records[-1]
    assert end["termination"] == "error"
    assert end["detail"] == "quota exceeded"


def test_recorder_follows_session_switches():
    first, second = FakeStore(), FakeStore()
    holder = {"session": first}
    recorder = SessionMetaRecorder(lambda: holder["session"])
    recorder(AgentEvent(type="agent_start"))
    holder["session"] = second
    recorder(AgentEvent(type="agent_end", turns=0, termination="aborted"))
    assert [record["type"] for record in first.records] == ["run_start"]
    assert [record["type"] for record in second.records] == ["run_end"]


def test_retry_recorder_shape():
    store = FakeStore()
    on_retry = make_retry_recorder(lambda: store)
    on_retry(assistant(stop="error", error="connection reset"), 2, 5, 0.25)
    (record,) = store.records
    assert record == {
        "type": "retry",
        "timestamp": record["timestamp"],
        "attempt": 2,
        "max_retries": 5,
        "delay_ms": 250,
        "error": "connection reset",
        "model": "m",
    }


def test_mode_record_shape():
    assert mode_record("plan") == {"type": "mode", "mode": "plan"}


# ---------------------------------------------------------------------------
# Integration: a real run writes the records into the session file
# ---------------------------------------------------------------------------


async def test_a_run_persists_header_run_records_and_mode(session_runtime):
    runtime = session_runtime
    await runtime.agent.prompt("hello")

    reloaded = Session(runtime.session.path)
    reloaded.load()
    assert reloaded.warnings == []
    kinds = [record["type"] for record in reloaded.records]
    assert kinds == ["session", "mode", "run_start", "run_end"]
    header = reloaded.header
    assert header["app"] == "zenagent"
    assert header["cwd"] == str(Path.cwd())
    assert isinstance(header["createdAt"], int)
    assert header["version"] == 1

    end = reloaded.records[-1]
    assert end["termination"] == "stop"
    assert end["turns"] == 1
    assert isinstance(end["duration_ms"], int)
    # The messages themselves are still pure Message lines.
    assert [m.content for m in reloaded.messages if isinstance(m, UserMessage)] == ["hello"]


async def test_set_mode_appends_a_mode_record(session_runtime):
    runtime = session_runtime
    runtime.set_mode("plan")
    reloaded = Session(runtime.session.path)
    reloaded.load()
    modes = [record for record in reloaded.records if record["type"] == "mode"]
    assert len(modes) == 2
    assert modes[-1]["mode"] == "plan"


async def test_transient_retries_leave_retry_records(runtime_factory, app_env):
    attempts = {"n": 0}

    def flaky_stream():
        async def stream(model, messages, tools, options):
            attempts["n"] += 1
            partial = AssistantMessage(
                content=[], provider="p", model="m", usage=Usage(),
                stop_reason="pending", timestamp=0,
            )
            yield AssistantEvent(type="start", partial=partial)
            if attempts["n"] < 2:
                failed = AssistantMessage(
                    content=[], provider="p", model="m", usage=Usage(),
                    stop_reason="error", timestamp=0,
                    error_message="connection reset by peer",
                )
                yield AssistantEvent(type="error", partial=failed, reason="error")
                return
            partial.content = [TextContent(text="recovered")]
            partial.stop_reason = "stop"
            yield AssistantEvent(type="done", partial=partial, reason="stop")

        return stream

    runtime = await runtime_factory(stream_fn=flaky_stream(), retries=2)
    await runtime.agent.prompt("flaky please")

    reloaded = Session(runtime.session.path)
    reloaded.load()
    retries = [record for record in reloaded.records if record["type"] == "retry"]
    assert len(retries) == 1
    assert retries[0]["attempt"] == 1
    assert retries[0]["max_retries"] == 2
    assert retries[0]["error"] == "connection reset by peer"
    end = reloaded.records[-1]
    assert end["termination"] == "stop"
    assert "recovered" in json.dumps([m.to_json() for m in reloaded.messages])


async def test_errored_run_persists_termination_and_detail(runtime_factory, app_env):
    def failing_stream():
        async def stream(model, messages, tools, options):
            partial = AssistantMessage(
                content=[], provider="p", model="m", usage=Usage(),
                stop_reason="pending", timestamp=0,
            )
            yield AssistantEvent(type="start", partial=partial)
            failed = AssistantMessage(
                content=[], provider="p", model="m", usage=Usage(),
                stop_reason="error", timestamp=0,
                error_message="insufficient quota",
            )
            yield AssistantEvent(type="error", partial=failed, reason="error")

        return stream

    runtime = await runtime_factory(stream_fn=failing_stream())
    await runtime.agent.prompt("doomed")

    reloaded = Session(runtime.session.path)
    reloaded.load()
    end = reloaded.records[-1]
    assert end["type"] == "run_end"
    assert end["termination"] == "error"
    assert end["detail"] == "insufficient quota"


# ---------------------------------------------------------------------------
# Provenance: derived sessions record where they came from
# ---------------------------------------------------------------------------


async def test_fork_records_derived_from(services):
    runtime = services.runtime
    await runtime.agent.prompt("original")
    source_stem = runtime.session.path.stem
    forked_path = await services.sessions.fork()

    reloaded = Session(forked_path)
    reloaded.load()
    assert reloaded.header["derivedFrom"] == source_stem


async def test_compact_records_derived_from(runtime_factory, app_env, monkeypatch):
    from zenagent.app.compact import compact

    runtime = await runtime_factory(stream_fn=text_stream())
    await runtime.agent.prompt("long conversation incoming")
    source_stem = runtime.session.path.stem

    async def summary_stream(model, messages, tools, options):
        partial = AssistantMessage(
            content=[TextContent(text="a summary")], provider="p", model="m",
            usage=Usage(), stop_reason="pending", timestamp=0,
        )
        yield AssistantEvent(type="start", partial=partial)
        partial.stop_reason = "stop"
        yield AssistantEvent(type="done", partial=partial, reason="stop")

    path = await compact(runtime.agent, stream_fn=summary_stream, derived_from=source_stem)
    reloaded = Session(path)
    reloaded.load()
    assert reloaded.header["derivedFrom"] == source_stem
