"""Tool cards: one quiet row per call, coloured by state, expandable.

omp's plain-tinted card (``04-transcript-and-tools.md`` §2.4) is a status head
plus the collapse hint; success stays dim rather than turning green, and only
error/warning spend colour. The result shows its *tail* with an
``… N earlier lines`` marker, because the interesting part of tool output is
the end. Write/edit cards are the exception: their collapsed face previews the
change's first lines, because a file mutation's one-line result sentence says
nothing about what changed.
"""

from zenagent.app.views import ToolCallView
from zenagent.cli.tui.widgets.tool_card import (
    MAX_DIFF_PREVIEW_LINES,
    MAX_RESULT_LINES,
    ToolCard,
)


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
                        details={"path": "x.py", "diff": diff}, diff=diff))
    assert "x.py" in card.plain and "+2/-1" in card.plain


def diff_of(*body: str) -> str:
    return "--- a/x.py\n+++ b/x.py\n@@ -1,2 +1,3 @@\n" + "\n".join(body) + "\n"


def test_edit_card_previews_the_diff_without_opening():
    diff = diff_of(" keep", "-old", "+new", "+more")
    card = ToolCard(view(name="edit_file", args={"path": "x.py"},
                        details={"path": "x.py", "diff": diff}, diff=diff))
    # The preview is the card's default face: no key press needed.
    assert card.collapsed is True
    text = card.plain
    assert "-old" in text and "+new" in text
    # The head already carries the path, so the file heads stay out of the body.
    assert "--- a/x.py" not in text and "+++ b/x.py" not in text


def test_write_card_previews_a_created_file():
    diff = diff_of("+first", "+second")
    card = ToolCard(view(name="write_file", args={"path": "new.py"},
                        details={"path": "new.py", "diff": diff}, diff=diff))
    assert "+first" in card.plain and "+second" in card.plain


def test_diff_preview_keeps_five_body_lines_and_counts_the_rest():
    body = [f"+line {index}" for index in range(MAX_DIFF_PREVIEW_LINES + 3)]
    diff = diff_of(*body)
    card = ToolCard(view(name="edit_file", args={"path": "x.py"},
                        details={"path": "x.py", "diff": diff}, diff=diff))
    text = card.plain
    for line in body[:MAX_DIFF_PREVIEW_LINES]:
        assert line in text
    assert body[MAX_DIFF_PREVIEW_LINES] not in text
    assert f"{len(body) - MAX_DIFF_PREVIEW_LINES} more diff lines" in text


def test_expanded_edit_card_shows_the_whole_diff():
    body = [f"+line {index}" for index in range(MAX_DIFF_PREVIEW_LINES + 3)]
    diff = diff_of(*body)
    card = ToolCard(view(name="edit_file", args={"path": "x.py"},
                        details={"path": "x.py", "diff": diff}, diff=diff))
    card.set_collapsed(False)
    text = card.plain
    assert all(line in text for line in body)
    assert "more diff lines" not in text


def test_only_write_and_edit_preview_their_diff():
    diff = diff_of("+new")
    card = ToolCard(view(name="read_file", args={"path": "x.py"},
                        details={"path": "x.py", "diff": diff}, diff=diff))
    assert "+new" not in card.plain


def test_long_diff_line_is_clipped_in_the_preview():
    card = ToolCard(view(name="edit_file", args={"path": "x.py"},
                        details={"path": "x.py"}, diff=diff_of("+" + "x" * 500)))
    text = card.plain
    assert "x" * 500 not in text
    assert "…" in text


def test_diff_preview_appears_when_the_result_settles():
    # A running call has no diff yet; the card must pick it up on refresh.
    card = ToolCard(view(name="edit_file", args={"path": "x.py"}, status="running",
                        result_text=""))
    assert "-old" not in card.plain
    diff = diff_of("-old", "+new")
    card.view = view(name="edit_file", args={"path": "x.py"}, status="ok",
                     details={"path": "x.py", "diff": diff}, diff=diff)
    assert "-old" in card.plain and "+new" in card.plain


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


def test_cjk_progress_is_clipped_by_cell_width():
    from rich.cells import cell_len

    progress = "正在编译并链接整个工程" * 8  # wide glyphs: chars != cells
    card = ToolCard(view(status="running", result_text="", progress=progress))
    assert cell_len(card.plain) <= card._width()
