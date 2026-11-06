"""The shared tool-result helper: one shape, one truncation notice."""

from __future__ import annotations

from zenagent.tools._result import clip, text_result


def test_text_result_shape():
    result = text_result("hello")
    assert result.is_error is False
    assert result.details == {}
    assert result.content[0].text == "hello"


def test_text_result_carries_details_and_error():
    result = text_result("boom", is_error=True, exit_code=2)
    assert result.is_error is True
    assert result.details == {"exit_code": 2}


def test_clip_passes_short_text_through():
    assert clip("short", 100) == ("short", False)


def test_clip_truncates_with_the_house_notice():
    assert clip("x" * 200, 100) == ("x" * 100 + "\n… (truncated)", True)
