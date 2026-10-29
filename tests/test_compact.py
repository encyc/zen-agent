"""compact() tests: the transcript is replaced, the old file survives, failures change nothing."""

import time

import pytest

import zenagent.agent.session as session_module
from zenagent.agent import Agent
from zenagent.cli.compact import compact
from zenagent.events import AssistantEvent
from zenagent.providers.catalog import Model
from zenagent.providers.compat import CompatFlags
from zenagent.types import AssistantMessage, Cost, TextContent, Usage, UserMessage


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


@pytest.fixture(autouse=True)
def isolated_sessions(tmp_path, monkeypatch):
    root = tmp_path / "sessions"
    monkeypatch.setattr(session_module, "sessions_dir", lambda: root)
    return root


async def test_compact_replaces_transcript_and_writes_a_new_session(isolated_sessions):
    agent = agent_with_history(summary_stream("目标：改文件；决定：已改；未竟：无"))

    path = await compact(agent)

    assert path.parent == isolated_sessions
    assert path.name.endswith("-compact.jsonl")
    assert len(agent.messages) == 1
    only = agent.messages[0]
    assert isinstance(only, UserMessage)
    assert only.content.startswith("# 此前会话摘要")
    assert "改文件" in only.content
    # the new session file contains exactly that message
    from zenagent.agent.session import Session

    assert Session(path).load() == [only]


async def test_compact_failure_leaves_the_transcript_untouched(isolated_sessions):
    agent = agent_with_history(failing_stream("boom"))
    before = agent.messages

    with pytest.raises(RuntimeError, match="compaction failed"):
        await compact(agent)

    assert agent.messages == before
    assert not isolated_sessions.exists()


async def test_compact_empty_transcript_is_rejected():
    agent = Agent(model(), "system", api_key="k", stream_fn=summary_stream())
    with pytest.raises(ValueError, match="nothing to compact"):
        await compact(agent)


async def test_compact_empty_summary_is_rejected(isolated_sessions):
    agent = agent_with_history(summary_stream("   "))
    with pytest.raises(RuntimeError, match="empty summary"):
        await compact(agent)
    assert len(agent.messages) == 2


async def test_compact_uses_the_agent_stream_fn_by_default():
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
    await compact(agent)
    assert seen, "the agent's stream fn must be used when none is passed"
    # the summarization request carries the transcript inside the user message
    assert "把文件改一下" in seen[0][-1].content


# ---------------------------------------------------------------------------
# S1-T10: automatic compaction
# ---------------------------------------------------------------------------

from zenagent.cli.compact import maybe_auto_compact, should_auto_compact


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

    path = await maybe_auto_compact(agent, print_fn=lambda *_: None)

    assert path is not None and path.parent == isolated_sessions
    assert len(agent.messages) == 1
    assert agent.messages[0].content.startswith("# 此前会话摘要")


async def test_maybe_auto_compact_does_nothing_below_the_threshold(isolated_sessions):
    agent = _big_agent(summary_stream(), prompt_tokens=10)
    assert await maybe_auto_compact(agent, print_fn=lambda *_: None) is None
    assert len(agent.messages) == 2


async def test_maybe_auto_compact_failure_keeps_the_transcript(isolated_sessions):
    agent = _big_agent(failing_stream("nope"))
    before = agent.messages
    path = await maybe_auto_compact(agent, print_fn=lambda *_: None)
    assert path is None
    assert agent.messages == before


async def test_maybe_auto_compact_skips_while_streaming(isolated_sessions):
    agent = _big_agent(summary_stream())
    agent._run = object()  # pretend a run is in flight
    assert await maybe_auto_compact(agent, print_fn=lambda *_: None) is None
    agent._run = None


async def test_maybe_auto_compact_skips_an_empty_transcript():
    agent = Agent(model(), "system", api_key="k", stream_fn=summary_stream())
    assert await maybe_auto_compact(agent, print_fn=lambda *_: None) is None
