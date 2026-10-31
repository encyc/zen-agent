"""ChatController: streaming into view models, follow-up queue, steer, abort."""

import asyncio

import pytest

from zenagent.app.chat import ChatController
from zenagent.events import AssistantEvent
from zenagent.types import AssistantMessage, TextContent, Usage, UserMessage


def gated_stream(gate: asyncio.Event):
    async def stream(model, messages, tools, options):
        partial = AssistantMessage(
            content=[TextContent(text="")],
            provider="p",
            model="m",
            usage=Usage(),
            stop_reason="pending",
            timestamp=0,
        )
        yield AssistantEvent(type="start", partial=partial)
        await gate.wait()
        partial.content[0].text = "done"
        partial.stop_reason = "stop"
        yield AssistantEvent(
            type="text_delta", partial=partial, content_index=0, delta="done"
        )
        yield AssistantEvent(type="done", partial=partial, reason="stop")

    return stream


async def test_submit_streams_and_second_submit_queues(runtime_factory):
    gate = asyncio.Event()
    runtime = await runtime_factory(stream_fn=gated_stream(gate))
    changes: list[int] = []
    chat = ChatController(runtime, on_change=lambda: changes.append(1))

    task = asyncio.create_task(chat.submit("hi"))
    await asyncio.sleep(0.05)
    assert chat.status.running is True

    await chat.submit("follow up")  # running → queued
    assert chat.status.follow_ups == 1

    gate.set()
    await task
    await asyncio.sleep(0.1)  # queued message auto-sent after the first run

    assert changes
    assert chat.status.follow_ups == 0
    assert chat.transcript[0].text == "hi"
    assert chat.transcript[1].text == "done"


async def test_status_reports_cache_write_and_last_turn_timing(runtime_factory):
    async def stream(model, messages, tools, options):
        partial = AssistantMessage(
            content=[],
            provider="p",
            model="m",
            usage=Usage(input=100, output=20, cache_read=60, cache_write=40),
            stop_reason="pending",
            timestamp=0,
        )
        yield AssistantEvent(type="start", partial=partial)
        await asyncio.sleep(0.02)
        partial.content = [TextContent(text="hi")]
        partial.stop_reason = "stop"
        yield AssistantEvent(
            type="text_delta", partial=partial, content_index=0, delta="hi"
        )
        yield AssistantEvent(type="done", partial=partial, reason="stop")

    runtime = await runtime_factory(stream_fn=stream)
    chat = ChatController(runtime)
    await chat.submit("hi")
    await chat.wait_for_idle()

    status = chat.status
    assert status.cache_write == 40
    assert status.turn_ms >= 15 and status.turn_output_tokens == 20


def failing_stream(message: str | None = "provider exploded"):
    async def stream(model, messages, tools, options):
        partial = AssistantMessage(
            content=[],
            provider="p",
            model="m",
            usage=Usage(),
            stop_reason="error",
            timestamp=0,
            error_message=message,
        )
        yield AssistantEvent(type="start", partial=partial)
        yield AssistantEvent(type="error", partial=partial, reason="error")

    return stream


def error_then_text_stream():
    calls = 0

    async def stream(model, messages, tools, options):
        nonlocal calls
        calls += 1
        if calls == 1:
            partial = AssistantMessage(
                content=[],
                provider="p",
                model="m",
                usage=Usage(),
                stop_reason="error",
                timestamp=0,
                error_message="boom",
            )
            yield AssistantEvent(type="start", partial=partial)
            yield AssistantEvent(type="error", partial=partial, reason="error")
            return
        partial = AssistantMessage(
            content=[TextContent(text="ok")],
            provider="p",
            model="m",
            usage=Usage(),
            stop_reason="pending",
            timestamp=0,
        )
        yield AssistantEvent(type="start", partial=partial)
        partial.stop_reason = "stop"
        yield AssistantEvent(
            type="text_delta", partial=partial, content_index=0, delta="ok"
        )
        yield AssistantEvent(type="done", partial=partial, reason="stop")

    return stream


async def test_status_reports_a_provider_error_after_a_failed_run(runtime_factory):
    runtime = await runtime_factory(stream_fn=failing_stream())
    chat = ChatController(runtime)
    await chat.submit("hi")
    await chat.wait_for_idle()

    assert chat.status.last_error == "provider exploded"


async def test_submit_clears_the_previous_error(runtime_factory):
    runtime = await runtime_factory(stream_fn=error_then_text_stream())
    chat = ChatController(runtime)
    await chat.submit("first")
    await chat.wait_for_idle()
    assert chat.status.last_error == "boom"

    await chat.submit("second")
    await chat.wait_for_idle()
    assert chat.status.last_error == ""


async def test_dequeue_follow_up_takes_the_last_queued_message(runtime_factory):
    gate = asyncio.Event()
    runtime = await runtime_factory(stream_fn=gated_stream(gate))
    chat = ChatController(runtime)
    task = asyncio.create_task(chat.submit("first"))
    await asyncio.sleep(0.05)

    chat.queue_follow_up("one")
    chat.queue_follow_up("two")
    assert chat.dequeue_follow_up() == "two"
    assert chat.status.follow_ups == 1
    assert chat.dequeue_follow_up() == "one"
    assert chat.dequeue_follow_up() is None

    gate.set()
    await task


async def test_compact_now_switches_to_the_summary_session(runtime_factory, capsys):
    from conftest import sequenced_stream

    runtime = await runtime_factory(stream_fn=sequenced_stream(["hi", "summary text"]))
    chat = ChatController(runtime)
    await chat.submit("hello")
    await chat.wait_for_idle()
    old_path = runtime.session.path

    message = await chat.compact_now()

    assert message and "compacted" in message
    assert runtime.session.path != old_path and runtime.session.path.exists()
    assert len(runtime.agent.messages) == 1  # only the summary UserMessage
    notices = chat.take_notices()
    assert notices and notices[0].kind == "divider" and "compacted" in notices[0].text
    assert capsys.readouterr().out == "", "the TUI controller must never print"


async def test_compact_now_refuses_while_streaming(runtime_factory):
    gate = asyncio.Event()
    runtime = await runtime_factory(stream_fn=gated_stream(gate))
    chat = ChatController(runtime)
    task = asyncio.create_task(chat.submit("hi"))
    await asyncio.sleep(0.05)

    assert await chat.compact_now() is None

    gate.set()
    await task
    await chat.wait_for_idle()


async def test_auto_compaction_runs_after_a_turn(runtime_factory, monkeypatch):
    from conftest import sequenced_stream

    import zenagent.app.chat as chat_module

    monkeypatch.setattr(chat_module, "should_auto_compact", lambda *a, **k: True)
    runtime = await runtime_factory(stream_fn=sequenced_stream(["hi", "summary text"]))
    chat = ChatController(runtime)

    await chat.submit("hello")
    await chat.wait_for_idle()

    notices = chat.take_notices()
    assert notices and notices[0].kind == "divider"


def image_block():
    from zenagent.types import ImageContent

    return ImageContent(data="aGk=", mime_type="image/png")


async def test_submit_during_auto_compaction_is_queued_not_lost(
    runtime_factory, monkeypatch
):
    from conftest import sequenced_stream

    import zenagent.app.chat as chat_module

    real_compact = chat_module.compact
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_compact(agent, stream_fn=None):
        started.set()
        await release.wait()
        return await real_compact(agent, stream_fn)

    monkeypatch.setattr(chat_module, "compact", slow_compact)
    calls = {"n": 0}

    def first_turn_only(usage, window):
        calls["n"] += 1
        return calls["n"] == 1

    monkeypatch.setattr(chat_module, "should_auto_compact", first_turn_only)
    runtime = await runtime_factory(
        stream_fn=sequenced_stream(["first answer", "summary text", "second answer"])
    )
    chat = ChatController(runtime)
    task = asyncio.create_task(chat.submit("first"))
    await asyncio.wait_for(started.wait(), timeout=5)

    assert chat.status.running is True, "compaction must keep the controller busy"
    await chat.submit("second")
    assert chat.status.follow_ups == 1, "submit during compaction is queued"

    release.set()
    await task
    await chat.wait_for_idle()

    prompts = [
        message.content
        for message in runtime.agent.messages
        if message.to_json()["role"] == "user"
    ]
    assert any("second" in str(prompt) for prompt in prompts), "the queued turn must run"
    assert chat.status.follow_ups == 0


async def test_auto_compaction_waits_for_queued_follow_ups(runtime_factory, monkeypatch):
    import zenagent.app.chat as chat_module

    gate = asyncio.Event()
    requests: list[list] = []

    async def stream(model, messages, tools, options):
        requests.append(list(messages))
        partial = AssistantMessage(
            content=[TextContent(text=f"answer{len(requests)}")],
            provider="p",
            model="m",
            usage=Usage(),
            stop_reason="pending",
            timestamp=0,
        )
        yield AssistantEvent(type="start", partial=partial)
        if len(requests) == 1:
            await gate.wait()
        partial.stop_reason = "stop"
        yield AssistantEvent(type="done", partial=partial, reason="stop")

    monkeypatch.setattr(chat_module, "should_auto_compact", lambda *a, **k: True)
    runtime = await runtime_factory(stream_fn=stream)
    chat = ChatController(runtime)

    task = asyncio.create_task(chat.submit("first"))
    await asyncio.sleep(0.05)
    chat.queue_follow_up("second")
    gate.set()
    await task
    await chat.wait_for_idle()

    # The queued turn runs on the *uncompacted* transcript; compression comes after.
    queued_request = requests[1]
    roles = [message.to_json()["role"] for message in queued_request]
    assert roles[-3:] == ["user", "assistant", "user"]
    flat = [str(getattr(message, "content", "")) for message in queued_request]
    assert not any("此前会话摘要" in text for text in flat)
    assert len(requests) == 3, "summarizer runs only after the queue is drained"


async def test_queued_prompt_carries_its_attachments(runtime_factory):
    gate = asyncio.Event()
    requests: list[list] = []

    async def stream(model, messages, tools, options):
        requests.append(list(messages))
        partial = AssistantMessage(
            content=[TextContent(text=f"answer{len(requests)}")],
            provider="p",
            model="m",
            usage=Usage(),
            stop_reason="pending",
            timestamp=0,
        )
        yield AssistantEvent(type="start", partial=partial)
        if len(requests) == 1:
            await gate.wait()
        partial.stop_reason = "stop"
        yield AssistantEvent(type="done", partial=partial, reason="stop")

    runtime = await runtime_factory(stream_fn=stream)
    chat = ChatController(runtime)
    task = asyncio.create_task(chat.submit("first"))
    await asyncio.sleep(0.05)

    chat.attach_image(image_block())
    await chat.submit("queued with image")  # running → queued, must keep the image
    gate.set()
    await task
    await chat.wait_for_idle()

    user = requests[-1][-1]
    assert user.to_json()["role"] == "user"
    assert any(block.to_json()["type"] == "image" for block in user.content)
    assert chat.attachments == []


async def test_clear_drops_pending_attachments(runtime_factory):
    runtime = await runtime_factory()
    chat = ChatController(runtime)
    chat.attach_image(image_block())

    chat.clear()

    assert chat.attachments == []


async def test_cache_hit_ratio_uses_provider_usage_semantics(runtime_factory):
    async def stream(model, messages, tools, options):
        partial = AssistantMessage(
            content=[TextContent(text="hi")],
            provider="p",
            model="m",
            usage=Usage(input=1000, output=5, cache_read=750),
            stop_reason="pending",
            timestamp=0,
        )
        yield AssistantEvent(type="start", partial=partial)
        partial.stop_reason = "stop"
        yield AssistantEvent(type="done", partial=partial, reason="stop")

    runtime = await runtime_factory(stream_fn=stream)  # deepseek-flash: OpenAI-style
    chat = ChatController(runtime)
    await chat.submit("hi")
    await chat.wait_for_idle()

    assert chat.status.cache_hit_ratio == pytest.approx(0.75)


async def test_tool_update_events_populate_card_progress(runtime_factory):
    """bash streams partial output; the controller must expose it on the card."""
    from zenagent.agent.loop import ToolResultAgent
    from zenagent.app.views import ToolCallView
    from zenagent.events import AgentEvent
    from zenagent.types import TextContent

    runtime = await runtime_factory()
    changes: list[int] = []
    chat = ChatController(runtime, on_change=lambda: changes.append(1))

    chat._on_event(
        AgentEvent(
            type="tool_execution_start",
            tool_call_id="t1",
            tool_name="bash",
            args={"command": "make"},
        )
    )
    chat._on_event(
        AgentEvent(
            type="tool_execution_update",
            tool_call_id="t1",
            tool_name="bash",
            args={"command": "make"},
            partial_result=ToolResultAgent(
                content=[TextContent(text="compiling\nlinking 2 files")]
            ),
        )
    )

    card = chat.transcript[-1]
    assert isinstance(card, ToolCallView)
    assert card.progress == "linking 2 files"
    assert changes, "frontends must be notified about streamed progress"


async def test_tool_update_after_the_call_finished_is_ignored(runtime_factory):
    from zenagent.agent.loop import ToolResultAgent
    from zenagent.app.views import ToolCallView
    from zenagent.events import AgentEvent
    from zenagent.types import TextContent

    runtime = await runtime_factory()
    chat = ChatController(runtime)
    chat._on_event(
        AgentEvent(
            type="tool_execution_start",
            tool_call_id="t1",
            tool_name="bash",
            args={"command": "make"},
        )
    )
    chat._on_event(
        AgentEvent(
            type="tool_execution_end",
            tool_call_id="t1",
            tool_name="bash",
            args={"command": "make"},
            result=ToolResultAgent(content=[TextContent(text="done")]),
            is_error=False,
            duration_ms=5,
        )
    )
    chat._on_event(
        AgentEvent(
            type="tool_execution_update",
            tool_call_id="t1",
            tool_name="bash",
            args={"command": "make"},
            partial_result=ToolResultAgent(content=[TextContent(text="late")]),
        )
    )

    card = chat.transcript[-1]
    assert isinstance(card, ToolCallView)
    assert card.progress == ""


async def test_task_result_cost_is_surfaced_in_run_status(runtime_factory):
    from zenagent.agent.loop import ToolResultAgent
    from zenagent.app.chat import ChatController
    from zenagent.events import AgentEvent
    from zenagent.types import TextContent

    runtime = await runtime_factory()
    chat = ChatController(runtime)
    result = ToolResultAgent(
        content=[TextContent(text="<task-result …>")],
        details={"subagent_cost": 0.03, "subagent_tokens": 1500},
    )
    chat._on_event(
        AgentEvent(
            type="tool_execution_end",
            tool_call_id="t1",
            tool_name="task",
            result=result,
        )
    )
    status = chat.status
    assert status.subagent_cost == 0.03
    assert status.subagent_tokens == 1500


async def test_status_exposes_background_jobs(runtime_factory, monkeypatch):
    from zenagent.app.views import JobView

    runtime = await runtime_factory()
    chat = ChatController(runtime)

    class _Running:
        id = "bg_2"
        command = "pytest -q"
        status = "running"
        started_at = 20.0
        duration_ms = None
        exit_code = None

    class _Finished:
        id = "bg_1"
        command = "ls"
        status = "ok"
        started_at = 10.0
        duration_ms = 120
        exit_code = 0

    class _Manager:
        def list(self) -> list:
            return [_Finished(), _Running()]

    monkeypatch.setattr("zenagent.app.chat.get_job_manager", lambda: _Manager())

    views = chat.status.jobs

    assert [view.id for view in views] == ["bg_2", "bg_1"]
    assert all(isinstance(view, JobView) for view in views)
    assert views[1].duration_ms == 120


async def test_status_jobs_degrades_to_empty_without_a_loop(runtime_factory, monkeypatch):
    runtime = await runtime_factory()
    chat = ChatController(runtime)

    def no_loop():
        raise RuntimeError("no running event loop")

    monkeypatch.setattr("zenagent.app.chat.get_job_manager", no_loop)

    assert chat.status.jobs == []


async def test_status_lists_task_subagents_with_the_active_one_first(runtime_factory):
    from zenagent.agent.loop import ToolResultAgent
    from zenagent.app.views import ToolCallView
    from zenagent.events import AgentEvent
    from zenagent.types import TextContent

    runtime = await runtime_factory()
    chat = ChatController(runtime)

    # A non-task tool must not show up in the subagent section.
    chat._on_event(
        AgentEvent(
            type="tool_execution_start",
            tool_call_id="t1",
            tool_name="bash",
            args={"command": "make"},
        )
    )
    chat._on_event(
        AgentEvent(
            type="tool_execution_end",
            tool_call_id="t1",
            tool_name="bash",
            args={"command": "make"},
            result=ToolResultAgent(content=[TextContent(text="done")]),
            is_error=False,
            duration_ms=5,
        )
    )
    chat._on_event(
        AgentEvent(
            type="tool_execution_start",
            tool_call_id="t2",
            tool_name="task",
            args={"prompt": "go", "name": "research"},
        )
    )
    chat._on_event(
        AgentEvent(
            type="tool_execution_end",
            tool_call_id="t2",
            tool_name="task",
            args={"prompt": "go", "name": "research"},
            result=ToolResultAgent(
                content=[TextContent(text="<task-result …>")],
                details={
                    "status": "ok",
                    "duration_ms": 18400,
                    "cost": 0.0031,
                    "tokens": 812,
                },
            ),
            is_error=False,
            duration_ms=18400,
        )
    )
    chat._on_event(
        AgentEvent(
            type="tool_execution_start",
            tool_call_id="t3",
            tool_name="task",
            args={"prompt": "again", "name": "scan"},
        )
    )

    status = chat.status

    assert [view.call_id for view in status.subagents] == ["t3", "t2"]
    active, done = status.subagents
    assert (active.status, active.name) == ("running", "scan")
    assert active.duration_ms >= 0
    assert (done.status, done.name, done.cost, done.tokens) == (
        "ok",
        "research",
        0.0031,
        812,
    )
    assert status.active_subagent == "t3"
    # A live card has no sidecar yet: its sidebar row is not clickable.
    assert active.has_transcript is False

    card = next(
        view
        for view in chat.transcript
        if isinstance(view, ToolCallView) and view.call_id == "t3"
    )
    assert card.started_at is not None


def _subagent_event(kind, name="research", **payload):
    return (kind, {"name": name, "model": "m", **payload})


def _child_events():
    from zenagent.events import AgentEvent
    from zenagent.types import AssistantMessage, Usage, UserMessage

    partial = AssistantMessage(
        content=[],
        provider="p",
        model="m",
        usage=Usage(),
        stop_reason="pending",
        timestamp=1,
    )
    return [
        AgentEvent(type="message_start", message=UserMessage("go", 0)),
        AgentEvent(type="message_start", message=partial),
        AgentEvent(
            type="tool_execution_start",
            tool_call_id="tc1",
            tool_name="read_file",
            args={"path": "a.py"},
        ),
        AgentEvent(type="message_end", message=partial),
    ]


async def test_subagent_events_build_a_live_read_only_transcript(runtime_factory):
    runtime = await runtime_factory()
    chat = ChatController(runtime)

    from zenagent.events import AgentEvent

    chat._on_event(
        AgentEvent(
            type="tool_execution_start",
            tool_call_id="c1",
            tool_name="task",
            args={"prompt": "go", "name": "research"},
        )
    )
    chat.on_subagent_event("c1", "start", {"name": "research", "model": "m"})
    for event in _child_events():
        chat.on_subagent_event("c1", "event", {"event": event})
    chat.on_subagent_event(
        "c1",
        "end",
        {"name": "research", "status": "ok", "duration_ms": 1000, "cost": 0.01, "tokens": 9},
    )
    from zenagent.agent.loop import ToolResultAgent
    from zenagent.types import TextContent

    chat._on_event(
        AgentEvent(
            type="tool_execution_end",
            tool_call_id="c1",
            tool_name="task",
            args={"prompt": "go", "name": "research"},
            result=ToolResultAgent(
                content=[TextContent(text="<task-result …>")],
                details={
                    "status": "ok",
                    "duration_ms": 1000,
                    "cost": 0.01,
                    "tokens": 9,
                },
            ),
            is_error=False,
            duration_ms=1000,
        )
    )

    views = chat.subagent_transcript("c1")
    assert views is not None
    assert any(getattr(view, "call_id", "") == "tc1" for view in views)

    [entry] = chat.status.subagents
    assert (entry.call_id, entry.status, entry.has_transcript) == ("c1", "ok", True)
    assert entry.cost == 0.01


async def test_subagent_transcript_reads_the_session_sidecar_after_resume(
    runtime_factory,
):
    from zenagent.agent.loop import ToolResultAgent
    from zenagent.agent.subagents import SubagentStore
    from zenagent.app.views import ToolCallView
    from zenagent.events import AgentEvent
    from zenagent.types import TextContent

    runtime = await runtime_factory()
    chat = ChatController(runtime)
    store = SubagentStore(chat._session_path())
    store.begin("c1", "research", "m")
    store.append("c1", [UserMessage("go", 0)])
    store.finish("c1", status="ok", duration_ms=18400, cost=0.0031, tokens=812)

    # Replayed history: the task card has no live details, only its call id.
    chat._on_event(
        AgentEvent(
            type="tool_execution_start",
            tool_call_id="c1",
            tool_name="task",
            args={"prompt": "go", "name": "research"},
        )
    )
    chat._on_event(
        AgentEvent(
            type="tool_execution_end",
            tool_call_id="c1",
            tool_name="task",
            args={"prompt": "go", "name": "research"},
            result=ToolResultAgent(content=[TextContent(text="<task-result …>")]),
            is_error=False,
            duration_ms=18400,
        )
    )

    views = chat.subagent_transcript("c1")
    assert views is not None and views[0].text == "go"

    [entry] = chat.status.subagents
    assert isinstance(chat.transcript[-1], ToolCallView)
    assert (entry.status, entry.cost, entry.tokens, entry.has_transcript) == (
        "ok",
        0.0031,
        812,
        True,
    )


async def test_sidebar_hides_transcripts_the_transcript_no_longer_references(
    runtime_factory,
):
    from zenagent.agent.subagents import SubagentStore

    runtime = await runtime_factory()
    chat = ChatController(runtime)
    store = SubagentStore(chat._session_path())
    store.begin("ghost", "ghost", "m")
    store.finish("ghost", status="ok", duration_ms=1, cost=0.0, tokens=0)

    assert chat.status.subagents == []
    assert chat.subagent_transcript("ghost") is not None
