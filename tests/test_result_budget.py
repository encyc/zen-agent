"""Kernel-level result budget: every tool result goes through one truncation policy.

The per-tool caps (bash 30KB, fetch 12k chars, …) remain as a first line of
defense; this module is the safety net that also covers MCP/extension tools and
keeps the truncation notice uniform. It is pure data-in/data-out so the loop can
call it after the tool (and after ``after_tool_call``) without side effects.
"""

from __future__ import annotations

from pathlib import Path

from zenagent.agent.loop import ToolResultAgent
from zenagent.agent.result_budget import (
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


def test_small_single_line_result_is_untouched():
    """A page of minified HTML/JSON is one line; cutting it destroys the data."""
    line = "y" * 8_000
    budgeted = apply_result_budget(_text_result(line))
    assert _all_text(budgeted) == line
    assert budgeted.details.get("budget") is None


def test_over_budget_single_line_result_keeps_both_ends_and_caps_lines():
    line = "z" * (MAX_RESULT_BYTES + 20_000)
    budgeted = apply_result_budget(_text_result(line))

    rendered = _all_text(budgeted)
    assert len(rendered.encode("utf-8")) < len(line.encode("utf-8"))
    assert rendered.startswith("z") and rendered.endswith("z")
    assert "truncated" in rendered
    assert "line truncated" in rendered
    assert budgeted.details["budget"]["truncated_lines"] >= 1


def test_utf8_boundary_is_not_corrupted():
    text = "汉字内容" * 20_000  # 320 KB of 3-byte characters
    budgeted = apply_result_budget(_text_result(text))
    rendered = _all_text(budgeted)
    assert "\ufffd" not in rendered
    assert "汉字" in rendered


def test_a_30kb_bash_style_result_is_not_double_truncated():
    """bash's own 30KB cap is below the kernel budget; the kernel must no-op."""
    text = "line\n" * 6_000  # 30 KB, many short lines
    budgeted = apply_result_budget(_text_result(text))
    assert budgeted is not None
    assert _all_text(budgeted) == text
    assert budgeted.details.get("budget") is None


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


def test_error_kind_survives_budgeting():
    result = ToolResultAgent(
        content=[TextContent(text="e" * (MAX_RESULT_BYTES + 1))],
        is_error=True,
        error_kind="timeout",
    )
    budgeted = apply_result_budget(result)
    assert budgeted.error_kind == "timeout"


# ---------------------------------------------------------------------------
# v0.4.1: spill the full payload to an artifact before truncating it
# ---------------------------------------------------------------------------


def _recording_sink(path):
    calls: list[tuple[str, str]] = []

    def sink(tool_name: str, text: str):
        calls.append((tool_name, text))
        return path

    return sink, calls


def test_over_budget_result_is_saved_before_truncation_and_points_at_the_file(
    tmp_path,
):
    text = "HEAD-" + ("x" * 100_000) + "-TAIL"
    artifact = tmp_path / "artifacts" / "bash-1.txt"
    sink, calls = _recording_sink(artifact)

    budgeted = apply_result_budget(
        _text_result(text), tool_name="bash", save_artifact=sink
    )

    # The sink receives the COMPLETE payload, not the truncated view.
    assert calls == [("bash", text)]
    rendered = _all_text(budgeted)
    assert f"[full output saved: {artifact}]" in rendered
    assert rendered.startswith("HEAD-") and rendered.endswith("-TAIL")
    assert "truncated" in rendered
    assert budgeted.details["artifact"] == str(artifact)
    assert budgeted.details["budget"]["truncated"] is True


def test_sink_returning_none_degrades_to_plain_truncation():
    text = "HEAD-" + ("x" * 100_000) + "-TAIL"
    budgeted = apply_result_budget(
        _text_result(text),
        tool_name="bash",
        save_artifact=lambda tool_name, full_text: None,
    )

    rendered = _all_text(budgeted)
    assert "truncated" in rendered
    assert "full output saved" not in rendered
    assert budgeted.details.get("artifact") is None
    assert budgeted.details["budget"]["truncated"] is True


def test_sink_exception_degrades_to_plain_truncation():
    text = "HEAD-" + ("x" * 100_000) + "-TAIL"

    def broken_sink(tool_name, full_text):
        raise RuntimeError("disk exploded")

    budgeted = apply_result_budget(
        _text_result(text), tool_name="bash", save_artifact=broken_sink
    )

    assert "full output saved" not in _all_text(budgeted)
    assert budgeted.details.get("artifact") is None
    assert budgeted.details["budget"]["truncated"] is True


def test_under_budget_result_is_never_saved():
    sink, calls = _recording_sink(Path("/tmp/never-used.txt"))
    budgeted = apply_result_budget(
        _text_result("small enough"), tool_name="bash", save_artifact=sink
    )

    assert calls == []
    assert _all_text(budgeted) == "small enough"
    assert budgeted.details.get("artifact") is None


def test_multiple_text_blocks_are_saved_joined_as_one_payload(tmp_path):
    first = "a" * (MAX_RESULT_BYTES // 2 + 1)
    second = "b" * (MAX_RESULT_BYTES // 2 + 1)
    result = ToolResultAgent(
        content=[TextContent(text=first), TextContent(text=second)]
    )
    sink, calls = _recording_sink(tmp_path / "artifact.txt")

    apply_result_budget(result, tool_name="mcp__files__read", save_artifact=sink)

    assert len(calls) == 1
    assert calls[0][0] == "mcp__files__read"
    assert calls[0][1] == first + "\n" + second


def test_already_budgeted_result_is_not_saved_again(tmp_path):
    text = "z" * (MAX_RESULT_BYTES + 1)
    first_sink, first_calls = _recording_sink(tmp_path / "first.txt")
    second_sink, second_calls = _recording_sink(tmp_path / "second.txt")

    once = apply_result_budget(_text_result(text), tool_name="bash", save_artifact=first_sink)
    twice = apply_result_budget(once, tool_name="bash", save_artifact=second_sink)

    assert len(first_calls) == 1
    assert second_calls == []
    assert _all_text(twice) == _all_text(once)
    assert twice.details["artifact"] == str(tmp_path / "first.txt")
