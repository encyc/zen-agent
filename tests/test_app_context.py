"""Context composition: exact block characters + estimated token split.

Providers report one prompt-token total per request, never a per-segment split;
the report keeps characters exact and labels the token split as an estimate.
"""

from types import SimpleNamespace

from zenagent.app.context import build_context_report
from zenagent.prompts import RenderedBlock, RenderedPrompt
from zenagent.types import Usage, UserMessage


def _report(*blocks: tuple[str, int]) -> RenderedPrompt:
    return RenderedPrompt(
        text="x",
        blocks=tuple(
            RenderedBlock(id=block_id, chars=chars, cache_class="stable", version="v")
            for block_id, chars in blocks
        ),
        version="v",
    )


def _tool(name: str = "read_file") -> SimpleNamespace:
    return SimpleNamespace(name=name, description="read a file", parameters={"type": "object"})


def _build(**overrides):
    defaults = {
        "prompt_report": _report(("system.identity", 400), ("context_files", 400)),
        "tools": [_tool()],
        "messages": [UserMessage(content="hello", timestamp=1)],
        "last_usage": Usage(input=300, cache_read=20),
        "context_used": 500,
        "window": 1000,
    }
    defaults.update(overrides)
    return build_context_report(**defaults)


def test_report_groups_blocks_and_keeps_characters_exact():
    report = _build()

    by_id = {item.id: item for item in report.slices}
    assert by_id["system"].chars == 400
    assert by_id["agents"].chars == 400
    assert by_id["system"].label == "system prompt"
    assert by_id["agents"].label == "AGENTS.md"
    assert by_id["tools"].tokens > 0
    assert by_id["session"].tokens > 0
    assert report.measured is True
    assert report.total_tokens == 500
    assert report.window == 1000


def test_percentages_fill_the_measured_total():
    report = _build()
    total = sum(item.pct for item in report.slices)
    assert 99.0 <= total <= 101.0
    assert report.slices[-1].id == "other", "the residual keeps the bar honest"

    # Estimates below the measured total leave a documented "other" slice.
    assert report.slices[-1].tokens == report.total_tokens - sum(
        item.tokens for item in report.slices[:-1]
    )


def test_estimates_larger_than_the_total_are_scaled_down():
    report = _build(context_used=10)

    assert all(item.tokens >= 0 for item in report.slices)
    assert "other" not in {item.id for item in report.slices}
    assert 99.0 <= sum(item.pct for item in report.slices) <= 101.0


def test_missing_usage_is_marked_as_estimated():
    report = _build(last_usage=None)

    assert report.measured is False
    assert report.total_tokens == 500


def test_missing_prompt_report_still_reports_tools_and_session():
    report = _build(prompt_report=None)

    ids = {item.id for item in report.slices}
    assert "system" not in ids and "agents" not in ids
    assert {"tools", "session"} <= ids


def test_zero_total_and_zero_window_do_not_divide_by_zero():
    report = _build(context_used=0, window=0)

    assert report.total_tokens == 0
    assert all(item.pct == 0 for item in report.slices)


def test_prompt_blocks_carry_their_own_token_estimate():
    from zenagent.prompts import PromptContext, compose_system

    rendered = compose_system(
        PromptContext(cwd=__import__("pathlib").Path("/tmp"), tool_names=("read_file",))
    )

    assert rendered.blocks
    assert all(block.tokens > 0 for block in rendered.blocks)
    assert rendered.blocks[0].chars > rendered.blocks[0].tokens


def test_public_text_estimator_matches_the_fitting_heuristic():
    from zenagent.providers.limits import estimate_text_tokens

    assert estimate_text_tokens("a" * 400) == 100
    assert estimate_text_tokens("中文" * 10) > 20  # CJK chars cost ~1 token each
