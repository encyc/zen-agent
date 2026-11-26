"""compact() tests: the summary lands in the same session file behind a
compaction boundary; the session keeps its identity and failures change
nothing."""

import time

import pytest

import zenagent.agent.session as session_module
from zenagent.agent import Agent
from zenagent.agent.session import Session, session_title
from zenagent.app import records
from zenagent.app.compact import (
    KEEP_RECENT_TOKENS,
    _choose_cut,
    _message_tokens,
    compact,
    manual_keep_recent,
)
from zenagent.events import AssistantEvent
from zenagent.providers.catalog import Model
from zenagent.providers.compat import CompatFlags
from zenagent.types import (
    AssistantMessage,
    Cost,
    TextContent,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
)


def model() -> Model:
    return Model(
        id="m",
        provider="test",
        api="openai-completions",
        base_url="https://api.test/v1",
        context_window=1000,
        max_tokens=100,
        reasoning=False,
        cost=Cost(),
        compat=CompatFlags(),
    )


def summary_stream(text: str = "摘要正文"):
    async def stream(model_, messages, tools, options):
        partial = AssistantMessage(
            content=[TextContent(text=text)],
            provider="test",
            model="m",
            usage=Usage(),
            stop_reason="stop",
            timestamp=int(time.time()),
        )
        yield AssistantEvent(type="done", partial=partial, reason="stop")

    return stream


def failing_stream(message: str = "provider down"):
    async def stream(model_, messages, tools, options):
        partial = AssistantMessage(
            content=[],
            provider="test",
            model="m",
            usage=Usage(),
            stop_reason="error",
            timestamp=int(time.time()),
            error_message=message,
        )
        yield AssistantEvent(type="error", partial=partial, reason="error")

    return stream


def agent_with_history(stream) -> Agent:
    agent = Agent(model(), "system", api_key="k", stream_fn=stream)
    agent.replace_messages(
        [
            UserMessage(content="把文件改一下", timestamp=1),
            AssistantMessage(
                content=[TextContent(text="改好了")],
                provider="test",
                model="m",
                usage=Usage(),
                stop_reason="stop",
                timestamp=2,
            ),
        ]
    )
    return agent


def live_session(agent: Agent, root) -> Session:
    """A session file holding exactly the agent's live transcript."""
    session = Session(root / "s.jsonl")
    for message in agent.messages:
        session._append_sync(message)
    return session


@pytest.fixture(autouse=True)
def isolated_sessions(tmp_path, monkeypatch):
    sessions_root = tmp_path / "sessions"
    monkeypatch.setattr(
        session_module, "sessions_dir", lambda *, root=None: sessions_root
    )
    return sessions_root


async def test_compact_appends_the_summary_to_the_same_session(isolated_sessions):
    agent = agent_with_history(summary_stream("目标：改文件；决定：已改；未竟：无"))
    session = live_session(agent, isolated_sessions)
    original = agent_with_history(summary_stream()).messages

    path = await compact(agent, session)

    assert path == session.path, "compaction keeps the session identity"
    # the summary leads the live view; the recent tail stays in context
    assert len(agent.messages) == 2
    summary = agent.messages[0]
    assert isinstance(summary, UserMessage)
    assert summary.content.startswith("# 此前会话摘要")
    assert "改文件" in summary.content
    assert agent.messages[1:] == original[1:]
    # Boundary-aware replay returns summary + tail...
    cut = records.open_session(path)
    assert cut.load() == agent.messages
    assert cut.segment_start == len(original)
    # ...while the file keeps the full history as the audit trail.
    assert Session(path).load() == original + agent.messages
    assert not list(isolated_sessions.glob("*-compact.jsonl"))


async def test_compact_failure_leaves_the_transcript_untouched(isolated_sessions):
    agent = agent_with_history(failing_stream("boom"))
    session = live_session(agent, isolated_sessions)
    before = agent.messages
    file_before = session.path.read_text()

    with pytest.raises(RuntimeError, match="compaction failed"):
        await compact(agent, session)

    assert agent.messages == before
    assert session.path.read_text() == file_before


async def test_compact_empty_transcript_is_rejected(isolated_sessions):
    agent = Agent(model(), "system", api_key="k", stream_fn=summary_stream())
    session = Session(isolated_sessions / "s.jsonl")
    with pytest.raises(ValueError, match="nothing to compact"):
        await compact(agent, session)


async def test_compact_empty_summary_is_rejected(isolated_sessions):
    agent = agent_with_history(summary_stream("   "))
    session = live_session(agent, isolated_sessions)
    file_before = session.path.read_text()

    with pytest.raises(RuntimeError, match="empty summary"):
        await compact(agent, session)

    assert len(agent.messages) == 2
    assert session.path.read_text() == file_before


async def test_compact_uses_the_agent_stream_fn_by_default(isolated_sessions):
    seen: list[list] = []

    async def stream(model_, messages, tools, options):
        seen.append(messages)
        partial = AssistantMessage(
            content=[TextContent(text="ok")],
            provider="test",
            model="m",
            usage=Usage(),
            stop_reason="stop",
            timestamp=int(time.time()),
        )
        yield AssistantEvent(type="done", partial=partial, reason="stop")

    agent = agent_with_history(stream)
    session = live_session(agent, isolated_sessions)
    await compact(agent, session)
    assert seen, "the agent's stream fn must be used when none is passed"
    # the summarization request carries the transcript inside the user message
    assert "把文件改一下" in seen[0][-1].content


async def test_compact_pins_the_session_title(isolated_sessions):
    agent = agent_with_history(summary_stream("摘要"))
    session = live_session(agent, isolated_sessions)

    await compact(agent, session)

    # The cut would otherwise make the derived title collapse to the summary's
    # own header line; the pin keeps the original brief.
    assert session_title(session.path) == "把文件改一下"


# ---------------------------------------------------------------------------
# S1-T10: automatic compaction
# ---------------------------------------------------------------------------

from zenagent.app.compact import maybe_auto_compact, should_auto_compact


def test_should_auto_compact_threshold():
    assert should_auto_compact(Usage(input=84), 100) is False
    assert should_auto_compact(Usage(input=85), 100) is True
    assert should_auto_compact(Usage(input=40, cache_read=45), 100) is True
    assert should_auto_compact(Usage(input=999), 0) is False
    assert should_auto_compact(None, 100) is False


def _big_agent(stream, context_window=100, prompt_tokens=90):
    agent = Agent(model(), "system", api_key="k", stream_fn=stream)
    agent.model.context_window = context_window
    agent.replace_messages(
        [
            UserMessage(content="do the thing", timestamp=1),
            AssistantMessage(
                content=[TextContent(text="done")],
                provider="test",
                model="m",
                usage=Usage(input=prompt_tokens),
                stop_reason="stop",
                timestamp=2,
            ),
        ]
    )
    return agent


async def test_maybe_auto_compact_triggers_above_the_threshold(isolated_sessions):
    agent = _big_agent(summary_stream("summary text"))
    session = live_session(agent, isolated_sessions)

    path = await maybe_auto_compact(agent, session, print_fn=lambda *_: None)

    assert path == session.path
    assert len(agent.messages) == 2
    assert agent.messages[0].content.startswith("# 此前会话摘要")
    assert records.open_session(path).load() == agent.messages


async def test_maybe_auto_compact_does_nothing_below_the_threshold(isolated_sessions):
    agent = _big_agent(summary_stream(), prompt_tokens=10)
    session = live_session(agent, isolated_sessions)
    assert (
        await maybe_auto_compact(agent, session, print_fn=lambda *_: None) is None
    )
    assert len(agent.messages) == 2


async def test_maybe_auto_compact_failure_keeps_the_transcript(isolated_sessions):
    agent = _big_agent(failing_stream("nope"))
    session = live_session(agent, isolated_sessions)
    before = agent.messages
    path = await maybe_auto_compact(agent, session, print_fn=lambda *_: None)
    assert path is None
    assert agent.messages == before


async def test_maybe_auto_compact_skips_while_streaming(isolated_sessions):
    agent = _big_agent(summary_stream())
    session = live_session(agent, isolated_sessions)
    agent._run = object()  # pretend a run is in flight
    assert (
        await maybe_auto_compact(agent, session, print_fn=lambda *_: None) is None
    )
    agent._run = None


async def test_maybe_auto_compact_skips_an_empty_transcript(isolated_sessions):
    agent = Agent(model(), "system", api_key="k", stream_fn=summary_stream())
    session = Session(isolated_sessions / "s.jsonl")
    assert (
        await maybe_auto_compact(agent, session, print_fn=lambda *_: None) is None
    )


async def test_compact_appends_the_boundary_record_before_the_summary(
    isolated_sessions,
):
    import json

    agent = agent_with_history(summary_stream("摘要"))
    session = live_session(agent, isolated_sessions)

    path = await compact(agent, session, reason="manual", tokens_before=1234)

    lines = [json.loads(line) for line in path.read_text().splitlines()]
    kinds = [line.get("type") or line.get("role") for line in lines]
    # record → summary → tail: the boundary always lands right before the
    # summary, and the kept tail follows it
    record_index = kinds.index("compaction")
    assert lines[record_index]["tokensBefore"] == 1234
    assert lines[record_index]["reason"] == "manual"
    assert "derivedFrom" not in lines[record_index]
    assert kinds[record_index + 1] == "user"
    assert "compaction" not in kinds[record_index + 1 :]


async def test_compact_defaults_tokens_before_to_the_last_usage(isolated_sessions):
    import json

    agent = agent_with_history(summary_stream("摘要"))
    session = live_session(agent, isolated_sessions)
    agent.messages[-1].usage = Usage(input=900, cache_read=100)

    path = await compact(agent, session)

    record = next(
        item
        for item in (json.loads(line) for line in path.read_text().splitlines())
        if item.get("type") == "compaction"
    )
    assert record["tokensBefore"] == 1000
    assert record["reason"] == "manual"


# -- cut-point discipline and summary merge (pi learnings ③) -----------------


class recording_stream:
    """Summary stream that records each summarizer request."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.requests: list[list] = []

    async def __call__(self, model_, messages, tools, options):
        self.requests.append(list(messages))
        partial = AssistantMessage(
            content=[TextContent(text=self.text)],
            provider="test",
            model="m",
            usage=Usage(),
            stop_reason="stop",
            timestamp=int(time.time()),
        )
        yield AssistantEvent(type="done", partial=partial, reason="stop")


def _assistant(text: str, timestamp: int, *, call: ToolCall | None = None) -> AssistantMessage:
    content: list = [TextContent(text=text)]
    if call is not None:
        content.append(call)
    return AssistantMessage(
        content=content,
        provider="test",
        model="m",
        usage=Usage(),
        stop_reason="stop",
        timestamp=timestamp,
    )


def test_choose_cut_honors_the_budget_from_the_end():
    messages = [
        UserMessage(content="x" * 2000, timestamp=1),
        UserMessage(content="y" * 2000, timestamp=2),
        UserMessage(content="z" * 2000, timestamp=3),
    ]
    # each message is ~500 estimated tokens: 150 fits one, 600 fits two
    assert _choose_cut(messages, keep_tokens=600) == 2
    # a budget nothing fits leaves no tail (the pre-tail behavior)
    assert _choose_cut(messages, keep_tokens=100) == 3
    # a huge budget still keeps a non-empty head to summarize
    assert _choose_cut(messages, keep_tokens=10**9) == 1


def test_choose_cut_never_orphans_tool_result():
    messages = [
        UserMessage(content="early", timestamp=1),
        _assistant("y" * 2000, 2, call=ToolCall(id="c1", name="bash", arguments={})),
        ToolResultMessage(
            tool_call_id="c1",
            tool_name="bash",
            content=[TextContent(text="out")],
            is_error=False,
            timestamp=3,
        ),
    ]

    cut = _choose_cut(messages, keep_tokens=150)

    tail = messages[cut:]
    call_ids = {
        block.id
        for message in tail
        if isinstance(message, AssistantMessage)
        for block in message.content
        if isinstance(block, ToolCall)
    }
    assert all(
        not isinstance(message, ToolResultMessage) or message.tool_call_id in call_ids
        for message in tail
    )
    # the whole round was pulled into the tail even though the assistant
    # message alone blew the budget
    assert cut == 1


def test_choose_cut_never_orphans_even_at_the_floor():
    # previous-summary mode (floor=2): the budget stops the cut right after
    # the summary, which would orphan the tool result in the tail — the cut
    # gives up the tail rather than poison the next request
    messages = [
        UserMessage(content="# 此前会话摘要\nold", timestamp=1),
        _assistant("y" * 2000, 2, call=ToolCall(id="c1", name="bash", arguments={})),
        ToolResultMessage(
            tool_call_id="c1",
            tool_name="bash",
            content=[TextContent(text="out")],
            is_error=False,
            timestamp=3,
        ),
    ]

    cut = _choose_cut(messages, keep_tokens=150, floor=2)

    tail = messages[cut:]
    call_ids = {
        block.id
        for message in tail
        if isinstance(message, AssistantMessage)
        for block in message.content
        if isinstance(block, ToolCall)
    }
    assert all(
        not isinstance(message, ToolResultMessage) or message.tool_call_id in call_ids
        for message in tail
    )
    assert cut == 3  # no tail: the pre-tail full-summarize behavior


async def test_compact_keeps_a_recent_tail(isolated_sessions):
    messages = [
        UserMessage(content="x" * 2000, timestamp=1),
        _assistant("y" * 2000, 2),
        UserMessage(content="u2", timestamp=3),
        _assistant("a2", 4),
    ]
    agent = Agent(model(), "system", api_key="k", stream_fn=summary_stream("S"))
    agent.replace_messages(messages)
    session = live_session(agent, isolated_sessions)

    await compact(agent, session, keep_recent_tokens=150)

    assert agent.messages == [agent.messages[0], messages[2], messages[3]]
    assert agent.messages[0].content.startswith("# 此前会话摘要")
    replay = records.open_session(session.path)
    assert replay.load() == agent.messages
    kept = next(
        record
        for record in replay.records
        if records.record_type(record) == records.COMPACTION
    )["kept"]
    assert kept == 2


async def test_compact_full_summarize_when_nothing_fits(isolated_sessions):
    agent = agent_with_history(summary_stream("S"))
    session = live_session(agent, isolated_sessions)
    original = agent_with_history(summary_stream()).messages

    await compact(agent, session, keep_recent_tokens=0)

    # keep budget zero: no tail, the live view is just the summary
    assert agent.messages == [agent.messages[0]]
    assert records.open_session(session.path).load() == [agent.messages[0]]
    assert Session(session.path).load() == original + agent.messages


async def test_compact_merges_into_the_previous_summary(isolated_sessions):
    agent = agent_with_history(summary_stream("第一份摘要内容"))
    session = live_session(agent, isolated_sessions)
    await compact(agent, session)

    stream = recording_stream("合并后的摘要")
    agent.stream_fn = stream
    await compact(agent, session)

    assert len(stream.requests) == 1
    prompt = stream.requests[0][-1].content
    assert "已有摘要" in prompt
    assert "第一份摘要内容" in prompt
    assert "改好了" in prompt
    assert "请把下面这段编码会话压缩" not in prompt
    merged = agent.messages[0]
    assert merged.content.startswith("# 此前会话摘要")
    assert "合并后的摘要" in merged.content


def test_manual_keep_recent_scales_and_is_capped():
    mid = [UserMessage(content="y" * 2000, timestamp=index) for index in range(10)]
    budget = manual_keep_recent(mid)
    total = sum(_message_tokens(message) for message in mid)
    assert budget == total // 4, "a normal transcript keeps a quarter verbatim"
    assert 0 < budget < total

    huge = [UserMessage(content="z" * 8000, timestamp=index) for index in range(60)]
    assert manual_keep_recent(huge) == KEEP_RECENT_TOKENS, "the auto cap still applies"


async def test_compact_forwards_the_agent_session_id_to_the_provider(
    isolated_sessions,
):
    """A gateway that routes on the session id needs it on the summary call too.

    The OpenAI-compatible provider only sends `x-opencode-session` when
    `options.session_id` is set; the summarizer built its own StreamOptions and
    dropped the field, so `/compact` failed with a 400 on opencode-go while
    every normal turn worked.
    """
    seen: list = []

    async def stream(model_, messages, tools, options):
        seen.append(options)
        async for event in summary_stream("summary")(model_, messages, tools, options):
            yield event

    agent = Agent(
        model(), "system", api_key="k", stream_fn=stream, session_id="sess-abc"
    )
    agent.replace_messages(
        [
            UserMessage(content="改一下", timestamp=1),
            AssistantMessage(
                content=[TextContent(text="好")],
                provider="test",
                model="m",
                usage=Usage(),
                stop_reason="stop",
                timestamp=2,
            ),
        ]
    )
    session = live_session(agent, isolated_sessions)
    await compact(agent, session, reason="manual")

    assert seen, "the summarizer never called the provider"
    assert seen[0].session_id == "sess-abc"


async def test_compact_sends_no_abort_token_because_no_run_owns_it(
    isolated_sessions,
):
    """The summarizer runs outside any agent run, so it has no token to inherit.

    Pinned rather than ignored: `_Run` mints a fresh AbortToken per run and
    compaction is not one of those, so an inherited token would be a token from
    a finished run -- and cancelling compaction has to stay the caller's
    decision, not a side effect of the previous turn's abort.
    """
    seen: list = []

    async def stream(model_, messages, tools, options):
        seen.append(options)
        async for event in summary_stream("summary")(model_, messages, tools, options):
            yield event

    agent = agent_with_history(stream)
    session = live_session(agent, isolated_sessions)
    await compact(agent, session, reason="manual")
    assert seen[0].abort is None
