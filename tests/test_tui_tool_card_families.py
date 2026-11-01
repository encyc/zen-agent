"""Tool-card head grammar for the newer tool families (one line each)."""

from zenagent.app.views import ToolCallView
from zenagent.cli.tui.widgets.rows import NoticeRow
from zenagent.cli.tui.widgets.tool_card import ToolCard


def card(name: str, args: dict | None = None, details: dict | None = None) -> ToolCard:
    return ToolCard(
        ToolCallView(
            call_id="c1",
            name=name,
            args_preview="{}",
            args=args or {},
            details=details or {},
            status="ok",
        )
    )


def test_task_card_shows_name_status_and_cost():
    detail = card("task", {"prompt": "x"}, {"name": "scan", "status": "ok", "cost": 0.0123})._family_detail()
    assert "scan" in detail and "ok" in detail and "$0.0123" in detail


def test_todo_card_shows_op_and_task_count():
    phases = [
        {"name": "Phase 1", "tasks": [{"content": "a", "status": "open"}, {"content": "b", "status": "done"}]}
    ]
    detail = card("todo", {"op": "view"}, {"op": "view", "phases": phases})._family_detail()
    assert "view" in detail and "2 tasks" in detail


def test_ask_card_shows_the_answer_count():
    detail = card(
        "ask",
        {"questions": [{"id": "q1"}]},
        {"answers": [{"id": "q1", "selected": ["yes"]}]},
    )._family_detail()
    assert "1 answers" in detail


def test_wait_card_shows_job_status_and_exit_code():
    detail = card(
        "wait", {"job_id": "bg_1"}, {"job_id": "bg_1", "status": "done", "exit_code": 0}
    )._family_detail()
    assert "bg_1" in detail and "done" in detail and "exit 0" in detail


def test_web_search_card_shows_query_results_and_provider():
    detail = card(
        "web_search",
        {"query": "python asyncio"},
        {"results": 5, "provider": "tavily"},
    )._family_detail()
    assert "python asyncio" in detail and "5 results" in detail and "tavily" in detail


def test_fetch_card_shows_url_and_extraction_method():
    detail = card(
        "fetch", {"url": "https://example.com"}, {"method": "article"}
    )._family_detail()
    assert "https://example.com" in detail and "article" in detail


def test_list_dir_card_shows_the_entry_count():
    detail = card("list_dir", {"path": "src"}, {"path": "src", "entries": 7})._family_detail()
    assert "src" in detail and "7 entries" in detail


def test_unknown_tool_falls_back_to_the_argument_preview():
    detail = card("mystery", {"a": 1})._family_detail()
    assert detail == "{}"


def test_notice_row_has_a_warning_level():
    warning = NoticeRow("careful", warning=True)
    assert (warning.plain, warning.level) == ("careful", "warning")
    assert NoticeRow("boom", error=True).level == "error"
    assert NoticeRow("fyi").level == "info"
