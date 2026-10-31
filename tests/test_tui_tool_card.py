"""Tool cards: one quiet row per call, coloured by state, expandable.

omp's plain-tinted card (``04-transcript-and-tools.md`` §2.4) is a status head
plus the collapse hint; success stays dim rather than turning green, and only
error/warning spend colour. The result shows its *tail* with an
``… N earlier lines`` marker, because the interesting part of tool output is
the end.
"""

from zenagent.app.views import ToolCallView
from zenagent.cli.tui.widgets.tool_card import MAX_RESULT_LINES, ToolCard


def view(**overrides) -> ToolCallView:
    base = {
        "call_id": "c1",
        "name": "bash",
        "args_preview": '{"command": "echo hi"}',
        "args": {"command": "echo hi"},
        "status": "ok",
        "result_text": "hi\n",
        "duration_ms": 19,
    }
    base.update(overrides)
    return ToolCallView(**base)


def test_collapsed_card_is_one_row_with_a_state_dot():
    card = ToolCard(view())
    assert card.region.height == 0 or card.size.height in (0, 1)
    text = card.plain
    assert "bash" in text and "echo hi" in text
    assert "●" in text, "ok uses a quiet dot"
    assert "19ms" in text


def test_running_and_error_use_different_dots():
    assert "◐" in ToolCard(view(status="running", result_text="")).plain
    assert "●" in ToolCard(view(status="error", result_text="boom")).plain


def test_expanded_result_keeps_the_tail_and_marks_the_cut():
    long_output = "\n".join(f"line {index}" for index in range(1, 41))
    card = ToolCard(view(result_text=long_output))
    card.set_collapsed(False)
    text = card.plain
    assert f"{40 - MAX_RESULT_LINES} earlier lines" in text
    assert "line 40" in text
    assert "line 1\n" not in text, "only the tail is kept"


def test_short_result_expands_without_a_cut_marker():
    card = ToolCard(view(result_text="hi\n"))
    card.set_collapsed(False)
    assert "earlier lines" not in card.plain
    assert "hi" in card.plain


def test_error_card_mentions_the_failure_state():
    card = ToolCard(view(status="error", result_text="boom", is_error=True))
    assert "failed" in card.plain or "error" in card.plain


def test_toggling_twice_returns_to_one_row():
    card = ToolCard(view(result_text="a\nb\nc"))
    card.action_toggle_card()
    assert card.collapsed is False
    card.action_toggle_card()
    assert card.collapsed is True


async def test_ctrl_o_expands_every_card_at_once(chat_app_with_one_tool):
    app = chat_app_with_one_tool
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await app.chat.submit("go")
        await app.chat.wait_for_idle()
        await pilot.pause()
        await pilot.pause()

        cards = list(app.query(ToolCard))
        assert cards and all(card.collapsed for card in cards)

        await pilot.press("ctrl+o")
        await pilot.pause()
        assert all(not card.collapsed for card in cards)

        await pilot.press("ctrl+o")
        await pilot.pause()
        assert all(card.collapsed for card in cards)


def test_bash_card_shows_the_command_and_exit_code():
    card = ToolCard(view(name="bash", args={"command": "pytest -q"},
                        details={"exit_code": 1, "duration_ms": 120}))
    assert "$" not in card.plain  # 只加信息，不改变卡形状
    assert "pytest -q" in card.plain and "exit 1" in card.plain


def test_edit_card_shows_diff_stats():
    diff = "--- a/x.py\n+++ b/x.py\n@@\n-old\n+new\n+more\n"
    card = ToolCard(view(name="edit_file", args={"path": "x.py"},
                        details={"path": "x.py", "diff": diff}))
    assert "x.py" in card.plain and "+2/-1" in card.plain


def test_read_card_shows_line_count_and_truncation():
    card = ToolCard(view(name="read_file", args={"path": "a.py"},
                        details={"total_lines": 400, "truncated": True}))
    assert "a.py" in card.plain and "400 lines" in card.plain and "truncated" in card.plain


def test_grep_card_shows_match_count():
    card = ToolCard(view(name="grep", args={"pattern": "TODO"},
                        details={"matches": 7}))
    assert "TODO" in card.plain and "7 matches" in card.plain


def test_cards_degrade_without_details_and_for_unknown_tools():
    # 历史回放没有 details，新工具名也可能出现：都必须回退到通用标题。
    assert "mystery" in ToolCard(view(name="mystery", details={})).plain
    assert "read_file" in ToolCard(view(name="read_file", details={})).plain


def test_long_bash_command_never_wraps_the_collapsed_row():
    card = ToolCard(view(name="bash", args={"command": "x" * 500},
                        details={"exit_code": 0}))
    assert card.size.height in (0, 1)


def test_running_card_shows_streamed_progress_in_the_head():
    card = ToolCard(
        view(status="running", result_text="", progress="linking 2 files")
    )
    assert "linking 2 files" in card.plain
