"""View models: message/transcript conversion with degraded history fields."""

from zenagent.app.views import ToolCallView, messages_to_views, usage_total
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
