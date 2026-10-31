"""Kernel-level result budget: every tool result goes through one truncation policy.

The per-tool caps (bash 30KB, fetch 12k chars, …) remain as a first line of
defense; this module is the safety net that also covers MCP/extension tools and
keeps the truncation notice uniform. It is pure data-in/data-out so the loop can
call it after the tool (and after ``after_tool_call``) without side effects.
"""

from __future__ import annotations

from zenagent.agent.loop import ToolResultAgent
from zenagent.agent.result_budget import (
    MAX_LINE_CHARS,
    MAX_RESULT_BYTES,
    apply_result_budget,
)
from zenagent.types import ImageContent, TextContent


def _text_result(text: str, **kwargs) -> ToolResultAgent:
    return ToolResultAgent(content=[TextContent(text=text)], **kwargs)


def _all_text(result: ToolResultAgent) -> str:
    return "".join(
        block.text for block in result.content if isinstance(block, TextContent)
    )


def test_small_result_passes_through_untouched():
    result = _text_result("hello world")
    budgeted = apply_result_budget(result)
    assert _all_text(budgeted) == "hello world"
    assert budgeted.details.get("budget") is None
    assert budgeted.is_error is False


def test_large_result_keeps_head_and_tail_and_says_so():
    text = "HEAD-" + ("x" * 100_000) + "-TAIL"
    budgeted = apply_result_budget(_text_result(text))

    rendered = _all_text(budgeted)
    assert len(rendered.encode("utf-8")) < len(text.encode("utf-8"))
    assert rendered.startswith("HEAD-")
    assert rendered.endswith("-TAIL")
    assert "truncated" in rendered
    assert str(len(text.encode("utf-8"))) in rendered  # original size is reported

    meta = budgeted.details["budget"]
    assert meta["truncated"] is True
    assert meta["original_bytes"] == len(text.encode("utf-8"))


def test_large_result_preserves_image_blocks():
    text = "x" * 100_000
    result = ToolResultAgent(
        content=[
            TextContent(text=text),
            ImageContent(data="AAA", mime_type="image/png"),
        ]
    )
    budgeted = apply_result_budget(result)

    images = [b for b in budgeted.content if isinstance(b, ImageContent)]
    assert len(images) == 1
    assert images[0].mime_type == "image/png"
    assert len(budgeted.content) == 2
    assert isinstance(budgeted.content[0], TextContent)


def test_overlong_line_is_capped_even_when_the_result_is_small():
    line = "y" * (MAX_LINE_CHARS + 500)
    budgeted = apply_result_budget(_text_result(line))

    rendered = _all_text(budgeted)
    assert len(rendered) <= MAX_LINE_CHARS + 64
    assert "line truncated" in rendered
    assert budgeted.details["budget"]["truncated_lines"] == 1


def test_many_normal_lines_under_the_threshold_are_not_touched():
    text = "\n".join(f"line {index}" for index in range(100))
    budgeted = apply_result_budget(_text_result(text))
    assert _all_text(budgeted) == text
    assert budgeted.details.get("budget") is None


def test_error_flag_and_details_survive_budgeting():
    text = "e" * (MAX_RESULT_BYTES + 1)
    result = ToolResultAgent(
        content=[TextContent(text=text)],
        details={"path": "/tmp/x"},
        is_error=True,
        terminate=True,
    )
    budgeted = apply_result_budget(result)

    assert budgeted.is_error is True
    assert budgeted.terminate is True
    assert budgeted.details["path"] == "/tmp/x"
    assert budgeted.details["budget"]["truncated"] is True


def test_budgeting_is_idempotent():
    text = "z" * (MAX_RESULT_BYTES + 1)
    once = apply_result_budget(_text_result(text))
    twice = apply_result_budget(once)
    assert _all_text(twice) == _all_text(once)
    assert twice.details["budget"]["truncated"] is True
