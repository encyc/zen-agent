"""View models: message/transcript conversion with degraded history fields."""

from zenagent.app.views import (
    JobView,
    RunStatus,
    SubagentView,
    ToolCallView,
    job_views,
    messages_to_views,
    sort_jobs,
    usage_total,
)
from zenagent.types import (
    AssistantMessage,
    Cost,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
)


def test_tool_call_is_paired_with_its_result_and_degrades_missing_fields():
    messages = [
        UserMessage(content="go", timestamp=1),
        AssistantMessage(
            content=[
                ThinkingContent(thinking="plan"),
                ToolCall(id="c1", name="read_file", arguments={"path": "a.py"}),
            ],
            provider="p",
            model="m",
            usage=Usage(input=10, output=5),
            stop_reason="tool_use",
            timestamp=2,
        ),
        ToolResultMessage(
            tool_call_id="c1",
            tool_name="read_file",
            content=[TextContent(text="print(1)")],
            is_error=False,
            timestamp=3,
        ),
    ]
    views = messages_to_views(messages)
    assert [type(view).__name__ for view in views] == [
        "MessageView",
        "MessageView",
        "ToolCallView",
    ]
    card = views[-1]
    assert isinstance(card, ToolCallView)
    assert card.call_id == "c1" and card.status == "ok"
    assert card.result_text == "print(1)"
    assert card.duration_ms is None and card.diff is None  # JSONL 没存这些


def test_usage_total_sums_input_output_and_cache():
    usage = usage_total(
        [
            AssistantMessage(
                content=[],
                provider="p",
                model="m",
                usage=Usage(input=10, output=5, cache_read=3, cost=Cost(total=0.01)),
                stop_reason="stop",
                timestamp=1,
            )
        ]
    )
    assert (usage.input, usage.output, usage.cache_read) == (10, 5, 3)


def test_cache_hit_ratio_uses_openai_semantics():
    from zenagent.app.support import cache_hit_ratio
    from zenagent.types import Usage

    # OpenAI-style: prompt_tokens already includes cached tokens.
    ratio = cache_hit_ratio(Usage(input=1000, cache_read=750), "openai-completions")
    assert ratio == 0.75


def test_cache_hit_ratio_uses_anthropic_semantics():
    from zenagent.app.support import cache_hit_ratio
    from zenagent.types import Usage

    # Anthropic-style: input_tokens excludes cache reads/writes.
    ratio = cache_hit_ratio(
        Usage(input=250, cache_read=750, cache_write=0), "anthropic-messages"
    )
    assert ratio == 0.75


def test_cache_hit_ratio_is_none_without_cache_reads():
    from zenagent.app.support import cache_hit_ratio
    from zenagent.types import Usage

    assert cache_hit_ratio(Usage(input=1000), "openai-completions") is None


def test_messages_to_views_hides_the_loop_guard_notice():
    from zenagent.app.views import messages_to_views
    from zenagent.types import UserMessage

    guard = UserMessage(content="[tool-loop guard] stop repeating", timestamp=0)
    real = UserMessage(content="hello", timestamp=1)
    views = messages_to_views([guard, real])
    assert [view.text for view in views] == ["hello"]


def test_user_view_recognises_an_injected_skill_message():
    from zenagent.app.views import user_view

    view = user_view(
        "[skill:pdf-report] review this\n\n[IMPORTANT: x]\nDo the thing."
    )
    assert view.kind == "skill"
    assert view.text == "pdf-report"
    assert view.skill_args == "review this"
    assert view.skill_body is not None
    assert "Do the thing." in view.skill_body

    plain = user_view("hello")
    assert plain.kind == "user" and plain.text == "hello"


class _FakeJob:
    """Duck-typed stand-in for ``zenagent.tools.jobs.Job``."""

    def __init__(
        self,
        job_id: str,
        *,
        command: str = "pytest -q",
        status: str = "running",
        started_at: float = 1.0,
        duration_ms: int | None = None,
        exit_code: int | None = None,
    ) -> None:
        self.id = job_id
        self.command = command
        self.status = status
        self.started_at = started_at
        self.duration_ms = duration_ms
        self.exit_code = exit_code


def test_run_status_defaults_have_no_jobs_or_subagents():
    status = RunStatus(model_id="m", thinking=None, running=False)
    assert status.jobs == []
    assert status.subagents == []
    assert status.active_subagent is None


def test_job_views_maps_each_job_to_a_view():
    job = _FakeJob("bg_1", status="ok", started_at=10.0, duration_ms=1200, exit_code=0)

    [view] = job_views([job])

    assert isinstance(view, JobView)
    assert (
        view.id,
        view.command,
        view.status,
        view.started_at,
        view.duration_ms,
        view.exit_code,
    ) == ("bg_1", "pytest -q", "ok", 10.0, 1200, 0)


def test_sort_jobs_puts_running_first_then_newest_finished():
    running = JobView(
        id="bg_2",
        command="a",
        status="running",
        started_at=5.0,
        duration_ms=None,
        exit_code=None,
    )
    old = JobView(
        id="bg_1", command="b", status="ok", started_at=1.0, duration_ms=10, exit_code=0
    )
    new = JobView(
        id="bg_3",
        command="c",
        status="failed",
        started_at=9.0,
        duration_ms=10,
        exit_code=1,
    )

    assert [view.id for view in sort_jobs([old, running, new])] == [
        "bg_2",
        "bg_3",
        "bg_1",
    ]


def test_tool_call_view_starts_without_a_live_start_time():
    card = ToolCallView(call_id="c1", name="bash", args_preview="{}")
    assert card.started_at is None


def test_subagent_view_carries_cost_and_transcript_flag():
    view = SubagentView(
        call_id="c1",
        name="research",
        status="ok",
        duration_ms=18400,
        cost=0.0031,
        tokens=812,
        has_transcript=True,
    )

    assert view.has_transcript is True
    assert view.cost == 0.0031
    assert view.tokens == 812


def test_transcript_builder_maps_streamed_events_to_views():
    from zenagent.app.views import TranscriptBuilder
    from zenagent.events import AgentEvent, AssistantEvent
    from zenagent.types import AssistantMessage, Usage

    builder = TranscriptBuilder()
    partial = AssistantMessage(
        content=[],
        provider="p",
        model="m",
        usage=Usage(),
        stop_reason="pending",
        timestamp=2,
    )

    builder.apply(AgentEvent(type="message_start", message=UserMessage("hi", 1)))
    builder.apply(AgentEvent(type="message_start", message=partial))
    builder.apply(
        AgentEvent(
            type="message_update",
            assistant_event=AssistantEvent(
                type="text_delta", partial=partial, content_index=0, delta="hello"
            ),
        )
    )
    partial.content = [TextContent(text="hello")]  # canonical final content
    builder.apply(AgentEvent(type="message_end", message=partial))

    assert [type(view).__name__ for view in builder.views] == [
        "MessageView",
        "MessageView",
    ]
    assert builder.views[0].kind == "user" and builder.views[0].text == "hi"
    assert builder.views[1].text == "hello"
    assert builder.current is None, "message_end must close the live row"


def test_transcript_builder_pairs_tool_calls_and_updates_progress():
    from zenagent.agent.loop import ToolResultAgent
    from zenagent.app.views import TranscriptBuilder
    from zenagent.events import AgentEvent
    from zenagent.types import TextContent

    builder = TranscriptBuilder()

    assert builder.apply(
        AgentEvent(
            type="tool_execution_start",
            tool_call_id="t1",
            tool_name="bash",
            args={"command": "make"},
        )
    )
    card = builder.cards["t1"]
    assert card.name == "bash" and card.started_at is not None

    builder.apply(
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
    assert builder.cards["t1"].progress == "linking 2 files"

    assert builder.apply(
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
    assert card.status == "ok"
    assert card.result_text == "done"
    assert card.duration_ms == 5


def test_transcript_builder_keeps_the_applied_diff_for_the_card():
    from zenagent.agent.loop import ToolResultAgent
    from zenagent.app.views import TranscriptBuilder
    from zenagent.events import AgentEvent
    from zenagent.types import TextContent

    builder = TranscriptBuilder()
    builder.apply(
        AgentEvent(
            type="tool_execution_start",
            tool_call_id="t1",
            tool_name="edit_file",
            args={"path": "x.py"},
        )
    )
    builder.apply(
        AgentEvent(
            type="tool_execution_end",
            tool_call_id="t1",
            tool_name="edit_file",
            args={"path": "x.py"},
            result=ToolResultAgent(
                content=[TextContent(text="edited x.py: replaced 1 occurrence")],
                details={"path": "x.py", "diff": "@@\n-old\n+new"},
            ),
            is_error=False,
            duration_ms=3,
        )
    )
    assert builder.cards["t1"].diff == "@@\n-old\n+new"

    # Only a non-empty string is card material; anything else stays absent.
    builder.apply(
        AgentEvent(
            type="tool_execution_start",
            tool_call_id="t2",
            tool_name="edit_file",
            args={"path": "y.py"},
        )
    )
    builder.apply(
        AgentEvent(
            type="tool_execution_end",
            tool_call_id="t2",
            tool_name="edit_file",
            args={"path": "y.py"},
            result=ToolResultAgent(
                content=[TextContent(text="edited y.py")], details={"diff": 5}
            ),
            is_error=False,
            duration_ms=3,
        )
    )
    assert builder.cards["t2"].diff is None


def test_transcript_builder_ignores_progress_after_the_call_settled():
    from zenagent.agent.loop import ToolResultAgent
    from zenagent.app.views import TranscriptBuilder
    from zenagent.events import AgentEvent
    from zenagent.types import TextContent

    builder = TranscriptBuilder()
    builder.apply(
        AgentEvent(
            type="tool_execution_start",
            tool_call_id="t1",
            tool_name="bash",
            args={"command": "make"},
        )
    )
    builder.apply(
        AgentEvent(
            type="tool_execution_end",
            tool_call_id="t1",
            tool_name="bash",
            result=ToolResultAgent(content=[TextContent(text="done")]),
            is_error=False,
            duration_ms=5,
        )
    )

    changed = builder.apply(
        AgentEvent(
            type="tool_execution_update",
            tool_call_id="t1",
            tool_name="bash",
            partial_result=ToolResultAgent(content=[TextContent(text="late")]),
        )
    )

    assert changed is False
    assert builder.cards["t1"].progress == ""


def test_subagent_view_defaults_to_a_complete_transcript():
    view = SubagentView(
        call_id="c1", name="research", status="ok", duration_ms=1, cost=0.0, tokens=0
    )

    assert view.transcript_truncated is False


def test_subagent_view_carries_the_transcript_truncation_flag():
    from zenagent.agent.subagents import SubagentMeta
    from zenagent.app.subagent_tracker import _subagent_view

    card = ToolCallView(
        call_id="c1",
        name="task",
        args_preview="{}",
        args={"name": "research", "prompt": "go"},
    )
    meta = SubagentMeta(
        call_id="c1",
        name="research",
        model="m",
        started_at=0.0,
        status="ok",
        duration_ms=1,
        cost=0.0,
        tokens=0,
        truncated=True,
    )

    view = _subagent_view(card, meta=meta, has_transcript=True)

    assert view.transcript_truncated is True


def test_steering_and_timestamp_are_projected_onto_user_views():
    views = messages_to_views(
        [
            UserMessage(content="ask", timestamp=10),
            UserMessage(content="nudge", timestamp=11, steering=True),
        ]
    )
    assert [(view.timestamp, view.steering) for view in views] == [
        (10, False),
        (11, True),
    ]


def test_view_ids_are_reload_deterministic():
    messages = [
        UserMessage(content="go", timestamp=1),
        AssistantMessage(
            content=[
                TextContent(text="reading"),
                ToolCall(id="c1", name="read_file", arguments={}),
            ],
            provider="p",
            model="m",
            usage=Usage(),
            stop_reason="tool_use",
            timestamp=2,
        ),
        ToolResultMessage(
            tool_call_id="c1",
            tool_name="read_file",
            content=[TextContent(text="ok")],
            is_error=False,
            timestamp=3,
        ),
    ]
    first = messages_to_views(messages)
    second = messages_to_views(messages)
    assert [view.id for view in first] == ["v1", "v2", "v3"]
    assert [view.id for view in second] == [view.id for view in first]


def test_assistant_metadata_is_projected_into_the_view():
    assistant = AssistantMessage(
        content=[TextContent(text="hi")],
        provider="anthropic",
        model="claude-x",
        usage=Usage(input=3, output=2),
        stop_reason="stop",
        timestamp=7,
        duration_ms=42,
    )
    (view,) = messages_to_views([assistant])
    assert view.id == "v1"
    assert (view.timestamp, view.model, view.provider, view.duration_ms) == (
        7,
        "claude-x",
        "anthropic",
        42,
    )
    assert view.usage is assistant.usage


def test_unpaired_tool_call_is_interrupted():
    assistant = AssistantMessage(
        content=[ToolCall(id="c1", name="bash", arguments={})],
        provider="p",
        model="m",
        usage=Usage(),
        stop_reason="tool_use",
        timestamp=2,
    )
    (card,) = messages_to_views([assistant])
    assert isinstance(card, ToolCallView)
    assert card.status == "interrupted"


def test_builder_drops_an_empty_assistant_row_at_message_end():
    from zenagent.app.views import TranscriptBuilder
    from zenagent.events import AgentEvent

    empty = AssistantMessage(
        content=[],
        provider="p",
        model="m",
        usage=Usage(),
        stop_reason="tool_use",
        timestamp=2,
    )
    builder = TranscriptBuilder()
    builder.apply(AgentEvent(type="message_start", message=empty))
    builder.apply(AgentEvent(type="message_end", message=empty))
    assert builder.views == []


def test_builder_keeps_a_thinking_only_row():
    from zenagent.app.views import TranscriptBuilder
    from zenagent.events import AgentEvent

    thinking = AssistantMessage(
        content=[ThinkingContent(thinking="hmm")],
        provider="p",
        model="m",
        usage=Usage(),
        stop_reason="stop",
        timestamp=2,
    )
    builder = TranscriptBuilder()
    builder.apply(AgentEvent(type="message_start", message=thinking))
    builder.apply(AgentEvent(type="message_end", message=thinking))
    assert [view.thinking for view in builder.views] == ["hmm"]


def test_builder_finalize_drops_empty_row_and_interrupts_running_cards():
    from zenagent.app.views import TranscriptBuilder
    from zenagent.events import AgentEvent

    empty = AssistantMessage(
        content=[],
        provider="p",
        model="m",
        usage=Usage(),
        stop_reason="pending",
        timestamp=2,
    )
    builder = TranscriptBuilder()
    builder.apply(AgentEvent(type="message_start", message=empty))
    builder.apply(
        AgentEvent(
            type="tool_execution_start",
            tool_call_id="c1",
            tool_name="bash",
            args={"command": "sleep 9"},
        )
    )
    builder.finalize()
    assert [view.id for view in builder.views] == ["v2"]
    assert builder.cards["c1"].status == "interrupted"


def test_builder_ids_keep_positions_when_an_empty_row_is_dropped():
    from zenagent.app.views import TranscriptBuilder
    from zenagent.events import AgentEvent

    empty = AssistantMessage(
        content=[],
        provider="p",
        model="m",
        usage=Usage(),
        stop_reason="tool_use",
        timestamp=2,
    )
    builder = TranscriptBuilder()
    builder.apply(
        AgentEvent(
            type="message_start",
            message=UserMessage(content="hi", timestamp=1),
        )
    )
    builder.apply(AgentEvent(type="message_start", message=empty))
    builder.apply(AgentEvent(type="message_end", message=empty))
    builder.apply(
        AgentEvent(
            type="tool_execution_start",
            tool_call_id="c1",
            tool_name="bash",
            args={},
        )
    )
    assert [view.id for view in builder.views] == ["v1", "v3"]


def test_load_hydrates_cards_from_tool_end_records():
    from zenagent.app.views import TranscriptBuilder

    messages = [
        UserMessage(content="go", timestamp=1),
        AssistantMessage(
            content=[ToolCall(id="c1", name="edit_file", arguments={})],
            provider="p",
            model="m",
            usage=Usage(),
            stop_reason="tool_use",
            timestamp=2,
        ),
        ToolResultMessage(
            tool_call_id="c1",
            tool_name="edit_file",
            content=[TextContent(text="edited")],
            is_error=False,
            timestamp=3,
        ),
    ]
    records = [
        {
            "type": "tool_end",
            "call_id": "c1",
            "duration_ms": 812,
            "details": {"diff": "+x", "exit_code": 0},
        }
    ]
    builder = TranscriptBuilder()
    builder.load(messages, records)
    card = builder.cards["c1"]
    assert (card.status, card.duration_ms, card.diff) == ("ok", 812, "+x")
    assert card.details["exit_code"] == 0


def test_orphan_and_unknown_records_are_ignored():
    from zenagent.app.views import TranscriptBuilder

    messages = [
        AssistantMessage(
            content=[ToolCall(id="c1", name="bash", arguments={})],
            provider="p",
            model="m",
            usage=Usage(),
            stop_reason="tool_use",
            timestamp=2,
        ),
        ToolResultMessage(
            tool_call_id="c1",
            tool_name="bash",
            content=[TextContent(text="ok")],
            is_error=False,
            timestamp=3,
        ),
    ]
    builder = TranscriptBuilder()
    builder.load(
        messages,
        [
            {"type": "tool_end", "call_id": "gone", "duration_ms": 1},
            {"type": "future_record"},
        ],
    )
    assert builder.cards["c1"].duration_ms is None


def test_last_tool_end_record_wins():
    from zenagent.app.views import TranscriptBuilder

    messages = [
        AssistantMessage(
            content=[ToolCall(id="c1", name="bash", arguments={})],
            provider="p",
            model="m",
            usage=Usage(),
            stop_reason="tool_use",
            timestamp=2,
        ),
        ToolResultMessage(
            tool_call_id="c1",
            tool_name="bash",
            content=[TextContent(text="ok")],
            is_error=False,
            timestamp=3,
        ),
    ]
    builder = TranscriptBuilder()
    builder.load(
        messages,
        [
            {"type": "tool_end", "call_id": "c1", "duration_ms": 1},
            {"type": "tool_end", "call_id": "c1", "duration_ms": 2},
        ],
    )
    assert builder.cards["c1"].duration_ms == 2
