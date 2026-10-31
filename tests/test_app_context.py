"""Context composition: exact block characters + estimated token split.

Providers report one prompt-token total per request, never a per-segment split;
the report keeps characters exact and labels the token split as an estimate.
Percentages are shares of the **model window** (a context cannot be "80% of
itself").
"""

from types import SimpleNamespace

from zenagent.app.context import build_context_report
from zenagent.prompts import RenderedBlock, RenderedPrompt
from zenagent.types import Usage, UserMessage


def _report(*blocks: tuple[str, int, int]) -> RenderedPrompt:
    return RenderedPrompt(
        text="x",
        blocks=tuple(
            RenderedBlock(
                id=block_id,
                chars=chars,
                cache_class="stable",
                version="v",
                tokens=tokens,
            )
            for block_id, chars, tokens in blocks
        ),
        version="v",
    )


def _tool(name: str = "read_file") -> SimpleNamespace:
    return SimpleNamespace(
        name=name, description="read a file", parameters={"type": "object"}
    )


def _build(**overrides):
    defaults = {
        "prompt_report": _report(
            ("system.identity", 400, 100), ("context_files", 400, 200)
        ),
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
    assert by_id["system"].tokens == 100
    assert by_id["agents"].tokens == 200
    assert by_id["tools"].tokens > 0
    assert by_id["session"].tokens > 0
    assert report.measured is True
    assert report.total_tokens == 500
    assert report.window == 1000


def test_percentages_are_shares_of_the_model_window():
    report = _build()

    for item in report.slices:
        assert abs(item.pct - item.tokens / report.window * 100.0) < 1e-9

    # The bar's remainder is free space, so the occupied shares stay below 100.
    assert 0.0 < sum(item.pct for item in report.slices) <= 100.0


def test_the_unused_window_is_reported_as_free_space():
    report = _build()

    occupied = max(report.total_tokens, report.prompt_tokens)
    assert report.free_tokens == report.window - occupied
    assert report.free_tokens == 500


def test_an_estimated_total_treats_the_estimate_as_occupancy():
    report = _build(last_usage=None)

    assert report.measured is False
    assert report.total_tokens == report.prompt_tokens
    assert report.free_tokens == report.window - report.prompt_tokens


def test_estimates_above_the_window_do_not_go_negative_or_scale():
    report = _build(context_used=1000, window=100)

    assert report.free_tokens == 0
    for item in report.slices:
        assert item.pct == item.tokens / 100 * 100.0


def test_missing_prompt_report_still_reports_tools_and_session():
    report = _build(prompt_report=None)

    ids = {item.id for item in report.slices}
    assert "system" not in ids and "agents" not in ids
    assert {"tools", "session"} <= ids


def test_zero_total_and_zero_window_do_not_divide_by_zero():
    report = _build(context_used=0, window=0)

    assert report.total_tokens == 0
    assert report.free_tokens == 0
    assert all(item.pct == 0 for item in report.slices)


def test_prompt_blocks_carry_their_own_token_estimate():
    from pathlib import Path

    from zenagent.prompts import PromptContext, compose_system

    rendered = compose_system(
        PromptContext(cwd=Path("/tmp"), tool_names=("read_file",))
    )

    assert rendered.blocks
    assert all(block.tokens > 0 for block in rendered.blocks)
    assert rendered.blocks[0].chars > rendered.blocks[0].tokens


def test_public_text_estimator_matches_the_fitting_heuristic():
    from zenagent.providers.limits import estimate_text_tokens

    assert estimate_text_tokens("a" * 400) == 100
    assert estimate_text_tokens("中文" * 10) > 20  # CJK chars cost ~1 token each
