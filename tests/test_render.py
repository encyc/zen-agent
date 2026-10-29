"""Render layer tests: pure functions and the live renderer state machine."""


from rich.console import RenderableType
from rich.text import Text

from pyagent.agent.loop import ToolResultAgent
from pyagent.agent.session import Session
from pyagent.cli.render import (
    AssistantLiveRenderer,
    session_header,
    tool_progress_line,
    tool_result_line,
)
from pyagent.events import AssistantEvent
from pyagent.types import (
    AssistantMessage,
    Cost,
    TextContent,
    ThinkingContent,
    ToolCall,
    Usage,
    UserMessage,
)


def partial(content=None, **overrides) -> AssistantMessage:
    message = AssistantMessage(
        content=list(content or []),
        provider="test",
        model="m",
        usage=Usage(),
        stop_reason="pending",
        timestamp=0,
    )
    for key, value in overrides.items():
        setattr(message, key, value)
    return message


def event(kind: str, msg: AssistantMessage, **kwargs) -> AssistantEvent:
    return AssistantEvent(type=kind, partial=msg, **kwargs)


def test_renderer_accumulates_thinking_then_text():
    renderer = AssistantLiveRenderer()
    message = partial()
    thinking = ThinkingContent(thinking="")
    message.content.append(thinking)

    update = renderer.feed(event("thinking_start", message))
    assert isinstance(update.renderable, RenderableType)
    assert update.finished is False

    thinking.thinking += "step one"
    renderer.feed(event("thinking_delta", message, delta="step one"))
    assert renderer.thinking == "step one"

    text = TextContent(text="")
    message.content.append(text)
    text.text += "Hello"
    renderer.feed(event("text_delta", message, delta="Hello"))
    update = renderer.feed(event("done", message, reason="stop"))

    assert renderer.text == "Hello"
    assert update.finished is True
    assert update.renderable is not None


def test_renderer_renders_tool_calls_as_lines():
    renderer = AssistantLiveRenderer()
    message = partial(
        [ToolCall(id="c1", name="bash", arguments={"command": "ls -la"})]
    )
    update = renderer.feed(event("toolcall_start", message, tool_call=message.content[0]))
    assert update.finished is False
    assert update.renderable is not None


def test_renderer_with_no_content_returns_empty_text():
    renderer = AssistantLiveRenderer()
    update = renderer.feed(event("start", partial()))
    assert isinstance(update.renderable, Text)
    assert update.finished is False


def test_error_terminal_finishes_the_renderer():
    renderer = AssistantLiveRenderer()
    message = partial([TextContent(text="partial")])
    update = renderer.feed(event("error", message, reason="error"))
    assert update.finished is True


def test_usage_reasoning_tokens_used_in_thinking_title():
    renderer = AssistantLiveRenderer()
    message = partial(
        [ThinkingContent(thinking="why")],
        usage=Usage(reasoning=42),
    )
    renderer.feed(event("thinking_start", message))
    assert renderer.reasoning_tokens == 42


# ---------------------------------------------------------------------------
# one-line helpers
# ---------------------------------------------------------------------------


def test_tool_result_line_marks_success_and_failure():
    ok = ToolResultAgent(content=[TextContent(text="hello world\nsecond line")])
    line = tool_result_line("read_file", ok, duration_ms=12)
    assert line.startswith("✓ read_file 12ms")
    assert "hello world" in line and "second line" not in line

    bad = ToolResultAgent(content=[TextContent(text="boom")], is_error=True)
    assert tool_result_line("bash", bad, duration_ms=None).startswith("✗ bash  boom")


def test_tool_result_line_truncates_long_previews():
    ok = ToolResultAgent(content=[TextContent(text="x" * 500)])
    assert len(tool_result_line("read_file", ok)) < 150


def test_tool_progress_line_is_single_line_and_strips_control_chars():
    partial_result = ToolResultAgent(
        content=[TextContent(text="line1\n\x1b[31mred\x07 line2")]
    )
    line = tool_progress_line("bash", partial_result)
    assert "\n" not in line
    assert "\x1b" not in line and "\x07" not in line
    assert line.startswith("⚙ bash (")


def test_session_header_reports_name_and_count(tmp_path):
    session = Session(tmp_path / "20260101-000000-x.jsonl")
    session.append(UserMessage(content="hi", timestamp=1))
    header = session_header(session)
    assert "20260101-000000-x.jsonl" in header
    assert "1 messages" in header
    assert "session" in header


def test_render_diff_colours_added_and_removed_lines():
    from pyagent.cli.render import render_diff

    diff = "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+new\n context\n"
    rendered = render_diff(diff)
    assert isinstance(rendered, Text)
    plain = rendered.plain
    by_style: dict[str, str] = {}
    for span in rendered.spans:
        by_style.setdefault(str(span.style), "")
        by_style[str(span.style)] += plain[span.start : span.end]
    assert "+new\n" in by_style.get("green", "")
    assert "-old\n" in by_style.get("red", "")
    assert any(span.style == "cyan" for span in rendered.spans)


def test_render_diff_handles_empty_input():
    from pyagent.cli.render import render_diff

    assert isinstance(render_diff(""), Text)


# ---------------------------------------------------------------------------
# S1-T6: status line
# ---------------------------------------------------------------------------


def test_status_line_contains_every_field():
    from pyagent.cli.render import status_line

    usage = Usage(input=8000, output=500, cache_read=400, cost=Cost(total=0.00042))
    line = status_line(
        model_id="deepseek-flash",
        thinking="high",
        messages=12,
        usage=usage,
        context_window=1_048_576,
        session_name="20260101-000000.jsonl",
    )
    assert "deepseek-flash" in line
    assert "thinking high" in line
    assert "12 msgs" in line
    assert "ctx 8.4k/1.0M (1%)" in line
    assert "$0.0004" in line
    assert "20260101-000000.jsonl" in line


def test_status_line_without_a_context_window_omits_percent():
    from pyagent.cli.render import status_line

    line = status_line(
        model_id="m", thinking=None, messages=1, usage=Usage(), context_window=0
    )
    assert "thinking off" in line
    assert "%" not in line
    assert "$0.0000" in line


def test_context_percent_uses_real_prompt_tokens():
    from pyagent.cli.render import context_percent

    assert context_percent(Usage(input=500, cache_read=500), 10_000) == 10
    assert context_percent(Usage(), 0) is None
