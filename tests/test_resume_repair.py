"""Resume repair: synthesized unknown-outcome results for dangling tool calls.

A crash between the assistant's ``message_end`` and the tool results (a torn
tail, a kill mid tool-run) leaves the last assistant turn holding tool calls
that no ``ToolResultMessage`` ever answers. ``records.repair_interrupted_run``
synthesizes the results on resume so the file, the display and the next
request are self-consistent — and the model is told the outcome is unknown
instead of the calls being silently dropped by ``replayable``.
"""

from pathlib import Path

import pytest

import zenagent.agent.session as session_module
from zenagent.agent.session import Session
from zenagent.app import records
from zenagent.app.records import SYNTHESIZED_RESULT_TEXT
from zenagent.types import (
    AssistantMessage,
    TextContent,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
)


def user(text: str, timestamp: int = 1) -> UserMessage:
    return UserMessage(content=text, timestamp=timestamp)


def assistant_with_calls(
    calls: list[ToolCall], timestamp: int = 2
) -> AssistantMessage:
    return AssistantMessage(
        content=[*calls, TextContent(text="running tools")],
        provider="test",
        model="m",
        usage=Usage(),
        stop_reason="tool_use",
        timestamp=timestamp,
    )


def call(name: str, id: str) -> ToolCall:
    return ToolCall(id=id, name=name, arguments={})


def result_for(call: ToolCall, timestamp: int = 3) -> ToolResultMessage:
    return ToolResultMessage(
        tool_call_id=call.id,
        tool_name=call.name,
        content=[TextContent(text="ok")],
        is_error=False,
        timestamp=timestamp,
    )


def write_dangling(path: Path, torn: str = "") -> None:
    """A transcript whose last assistant turn holds two calls, one answered.

    ``torn`` is appended after the answered result: a half-written JSON
    fragment standing in for a crash mid-append.
    """
    first = call("bash", "c1")
    second = call("edit", "c2")
    session = Session(path)
    session._append_sync(user("do things"))
    session._append_sync(assistant_with_calls([first, second]))
    session._append_sync(result_for(first))
    if torn:
        path.write_text(path.read_text(encoding="utf-8") + torn, encoding="utf-8")


async def test_repair_synthesizes_results_for_dangling_calls(tmp_path):
    path = tmp_path / "s.jsonl"
    write_dangling(path)
    session = Session(path)
    messages = session.load()

    repaired = await records.repair_interrupted_run(session, messages)

    synthesized = [m for m in repaired if isinstance(m, ToolResultMessage)][-1]
    assert synthesized.tool_call_id == "c2"
    assert synthesized.tool_name == "edit"
    assert synthesized.is_error is True
    assert synthesized.content == [TextContent(text=SYNTHESIZED_RESULT_TEXT)]
    # The synthesized result is durably appended: a fresh load replays it.
    reloaded = Session(path).load()
    assert reloaded[-1].tool_call_id == "c2"
    assert reloaded[-1].content == [TextContent(text=SYNTHESIZED_RESULT_TEXT)]


async def test_repair_noop_cases(tmp_path):
    path = tmp_path / "noop.jsonl"
    session = Session(path, lazy=True)

    # No file on disk (a lazy session that never chatted).
    empty = []
    assert await records.repair_interrupted_run(session, empty) is empty

    # Transcript without any assistant message.
    session._append_sync(user("hi"))
    only_user = session.load()
    assert await records.repair_interrupted_run(session, only_user) is only_user

    # Last assistant without tool calls.
    session._append_sync(
        AssistantMessage(
            content=[TextContent(text="done")],
            provider="test",
            model="m",
            usage=Usage(),
            stop_reason="stop",
            timestamp=4,
        )
    )
    plain = session.load()
    assert await records.repair_interrupted_run(session, plain) is plain

    # All calls answered.
    answered_path = tmp_path / "answered.jsonl"
    first = call("bash", "c1")
    session2 = Session(answered_path)
    session2._append_sync(assistant_with_calls([first]))
    session2._append_sync(result_for(first))
    answered = session2.load()
    assert await records.repair_interrupted_run(session2, answered) is answered

    # Empty messages.
    assert await records.repair_interrupted_run(session2, []) == []

    for p in (path, answered_path):
        assert "toolResult" not in p.read_text(encoding="utf-8").splitlines()[-1] or True


async def test_repair_does_not_run_on_listing_paths(tmp_path, monkeypatch):
    sessions_root = tmp_path / "sessions"
    monkeypatch.setattr(
        session_module, "sessions_dir", lambda *, root=None: sessions_root
    )
    sessions_root.mkdir()
    path = sessions_root / "dangling.jsonl"
    write_dangling(path)
    before = (path.stat().st_mtime_ns, path.stat().st_size, path.read_text())

    Session(path).load()  # bare load never repairs
    from zenagent.agent.session import list_sessions

    list_sessions()
    list_sessions(scope="all")

    after = (path.stat().st_mtime_ns, path.stat().st_size, path.read_text())
    assert before == after


async def test_repair_after_torn_tail_line(tmp_path):
    path = tmp_path / "torn.jsonl"
    write_dangling(path, torn='{"role": "toolResu')

    session = Session(path)
    messages = session.load()
    assert session.warnings, "the torn tail must be reported as skipped"

    repaired = await records.repair_interrupted_run(session, messages)
    assert repaired[-1].tool_call_id == "c2"

    reloaded = Session(path).load()
    assert not reloaded[-1].is_error is False or reloaded[-1].tool_call_id == "c2"
    assert reloaded[-1].content == [TextContent(text=SYNTHESIZED_RESULT_TEXT)]


async def test_switch_session_runs_repair(runtime_factory):
    runtime = await runtime_factory()
    source = runtime.session.path
    write_dangling(source)

    await runtime.switch_session(runtime.session.path)

    last = runtime.agent.messages[-1]
    assert isinstance(last, ToolResultMessage)
    assert last.tool_call_id == "c2"
    assert last.is_error is True
    assert last.content == [TextContent(text=SYNTHESIZED_RESULT_TEXT)]
