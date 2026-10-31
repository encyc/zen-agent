"""Run metrics: accumulation, jsonl output, and content-free records."""

import json

from zenagent.events import AgentEvent
from zenagent.telemetry import MetricsRecorder, append_metrics
from zenagent.types import AssistantMessage, Cost, TextContent, Usage


def assistant(model="m", cost=0.01) -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text="hi")],
        provider="p",
        model=model,
        usage=Usage(input=10, output=5, cache_read=2, cost=Cost(total=cost)),
        stop_reason="stop",
        timestamp=0,
    )


def feed(recorder: MetricsRecorder, message: AssistantMessage) -> None:
    recorder(AgentEvent(type="turn_start"))
    recorder(AgentEvent(type="tool_execution_start", tool_name="bash"))
    recorder(AgentEvent(type="message_end", message=message))


def test_accumulates_turns_tools_usage_and_termination():
    recorder = MetricsRecorder("m")
    feed(recorder, assistant())
    feed(recorder, assistant())
    metrics = recorder.build("stop")

    assert metrics.model == "m"
    assert metrics.turns == 2
    assert metrics.tool_calls == 2
    assert metrics.input == 20 and metrics.output == 10
    assert metrics.cache_read == 4
    assert metrics.cost == 0.02
    assert metrics.termination == "stop"
    assert metrics.duration_ms >= 0


def test_jsonl_line_is_parseable_and_contains_no_prompt_text(tmp_path):
    recorder = MetricsRecorder("m")
    feed(recorder, assistant())
    path = append_metrics(tmp_path / "metrics.jsonl", recorder.build("stop"))

    line = path.read_text(encoding="utf-8").strip()
    payload = json.loads(line)
    assert payload["model"] == "m" and payload["termination"] == "stop"
    assert "messages" not in payload
    assert "hi" not in line  # no content recorded


def test_appends_one_line_per_run(tmp_path):
    path = tmp_path / "metrics.jsonl"
    for termination in ("stop", "error"):
        append_metrics(path, MetricsRecorder("m").build(termination))
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert [json.loads(line)["termination"] for line in lines] == ["stop", "error"]


def test_failed_run_still_records_termination():
    metrics = MetricsRecorder("m").build("max_turns")
    assert metrics.termination == "max_turns"


async def test_metrics_recorder_captures_tool_stats():
    from zenagent.events import AgentEvent
    from zenagent.telemetry import MetricsRecorder

    recorder = MetricsRecorder("m")
    recorder(
        AgentEvent(
            type="agent_end",
            tool_stats={"tools": {"bash": {"calls": 2, "errors": 1, "ms": 15}}},
        )
    )
    metrics = recorder.build("stop")
    assert metrics.tools["bash"]["calls"] == 2
    assert metrics.tools["bash"]["errors"] == 1


def test_metrics_recorder_records_subagent_cost_from_task_results():
    from zenagent.agent.loop import ToolResultAgent
    from zenagent.events import AgentEvent
    from zenagent.telemetry import MetricsRecorder
    from zenagent.types import TextContent

    recorder = MetricsRecorder("m")
    task_result = ToolResultAgent(
        content=[TextContent(text="<task-result …>")],
        details={"subagent_cost": 0.02, "subagent_tokens": 1234},
    )
    recorder(
        AgentEvent(
            type="tool_execution_end",
            tool_call_id="t1",
            tool_name="task",
            result=task_result,
        )
    )
    recorder(
        AgentEvent(
            type="tool_execution_end",
            tool_call_id="t2",
            tool_name="task",
            result=task_result,
        )
    )
    metrics = recorder.build("stop")
    assert metrics.subagent_cost == 0.04
    assert metrics.subagent_tokens == 2468
