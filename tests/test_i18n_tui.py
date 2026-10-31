"""TUI language switching and cell-aware clipping (CJK included)."""

import pytest
from rich.cells import cell_len

from zenagent import i18n


@pytest.fixture(autouse=True)
def _restore_english():
    yield
    i18n.set_locale("en")


async def test_language_command_switches_and_persists(fake_runtime, monkeypatch):
    from zenagent.cli.tui.app import ZenAgentApp
    from zenagent.cli.tui.widgets.transcript import TranscriptView

    saved: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "zenagent.settings.set_pref",
        lambda name, value: saved.append((name, value)),
    )
    monkeypatch.setattr("zenagent.settings.get_pref", lambda name, default=None: None)
    app = ZenAgentApp(fake_runtime)
    async with app.run_test() as pilot:
        await pilot.pause()
        transcript = app.query_one(TranscriptView)
        resets: list[int] = []
        original_reset = transcript.reset
        monkeypatch.setattr(
            transcript, "reset", lambda: (resets.append(1), original_reset())[1]
        )
        await app._dispatch_command("lang", "zh")
        await pilot.pause()
        assert i18n.active_locale() == "zh"
        assert resets, "switching language must repaint the rich chrome"
        assert ("language", "zh") in saved
        assert app._language_spec == "zh"


async def test_language_command_rejects_unknown_value(fake_runtime, monkeypatch):
    from zenagent.cli.tui.app import ZenAgentApp

    monkeypatch.setattr("zenagent.settings.get_pref", lambda name, default=None: None)
    app = ZenAgentApp(fake_runtime)
    async with app.run_test() as pilot:
        await pilot.pause()
        await app._dispatch_command("lang", "klingon")
        await pilot.pause()
        assert i18n.active_locale() == "en"
        assert app._language_spec == "auto"


async def test_language_picker_without_argument(fake_runtime, monkeypatch):
    from zenagent.cli.tui.app import ZenAgentApp

    monkeypatch.setattr("zenagent.settings.get_pref", lambda name, default=None: None)
    app = ZenAgentApp(fake_runtime)
    async with app.run_test() as pilot:
        await pilot.pause()
        await app._dispatch_command("lang", "")
        await pilot.pause()
        assert "language" in app._menus


async def test_startup_language_flag_applies(fake_runtime, monkeypatch):
    from zenagent.cli.tui.app import ZenAgentApp

    monkeypatch.setattr("zenagent.settings.get_pref", lambda name, default=None: None)
    app = ZenAgentApp(fake_runtime, language="zh")
    async with app.run_test() as pilot:
        await pilot.pause()
        assert i18n.active_locale() == "zh"


def test_language_slash_values():
    from zenagent.app.slash_args import argument_values

    values = [value for value, _ in argument_values("/lang")]
    assert values == ["auto", "en", "zh"]


def test_clip_cells_counts_terminal_cells():
    from zenagent.cli.tui.textutil import clip_cells

    assert clip_cells("abcdef", 6) == "abcdef"
    assert clip_cells("abcdef", 4) == "abc…"
    clipped = clip_cells("中文测试文本", 5)
    assert cell_len(clipped) <= 5
    assert clipped.endswith("…")
    assert clip_cells("anything", 0) == ""


async def test_chinese_sidebar_rows_fit_cells(fake_runtime, monkeypatch):
    from zenagent.cli.tui.app import ZenAgentApp
    from zenagent.cli.tui.widgets.sidebar import Sidebar

    monkeypatch.setattr("zenagent.settings.get_pref", lambda name, default=None: None)
    i18n.set_locale("zh")
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+b")
        await pilot.pause()
        sidebar = app.query_one(Sidebar)
        width = sidebar._content_width
        for row in sidebar.query(".sidebar-row"):
            assert cell_len(row.plain) <= width, row.plain


async def test_sidebar_and_welcome_speak_chinese(fake_runtime, monkeypatch):
    from zenagent.cli.tui.app import ZenAgentApp
    from zenagent.cli.tui.widgets.sidebar import Sidebar, SidebarSection
    from zenagent.cli.tui.widgets.welcome import WelcomeBlock

    monkeypatch.setattr("zenagent.settings.get_pref", lambda name, default=None: None)
    app = ZenAgentApp(fake_runtime, language="zh")
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        titles = [section.title for section in app.query(SidebarSection)]
        assert titles == ["会话", "模型", "运行时", "上下文", "待办", "子代理", "后台任务"]
        await pilot.press("ctrl+b")
        await pilot.pause()
        assert app.query_one(Sidebar)._section("session").title == "会话"
        welcome = app.query_one(WelcomeBlock).plain
        assert "/ 命令" in welcome


def _plain(static) -> str:
    from rich.text import Text

    content = static._Static__content  # what Static.update()/ctor stored
    return content.plain if isinstance(content, Text) else str(content)


async def test_settings_menu_is_chinese(fake_runtime, monkeypatch):
    from zenagent.cli.tui.app import ZenAgentApp

    monkeypatch.setattr("zenagent.settings.get_pref", lambda name, default=None: None)
    app = ZenAgentApp(fake_runtime, language="zh")
    async with app.run_test() as pilot:
        await pilot.pause()
        await app._dispatch_command("settings", "")
        await pilot.pause()
        picker = app._menus["settings"]
        labels = [item.label for item in picker.items]
        assert "切换模型…" in labels
        assert "主题…" in labels and "语言…" in labels


async def test_dialogs_and_overlays_are_chinese(fake_runtime, monkeypatch):
    from textual.widgets import Static

    from zenagent.app.context import ContextReport
    from zenagent.app.views import JobLogView
    from zenagent.cli.tui.app import ZenAgentApp
    from zenagent.cli.tui.screens.context_breakdown import ContextBreakdownScreen
    from zenagent.cli.tui.screens.job_log import JobLogScreen
    from zenagent.cli.tui.screens.modals import ToolApproval
    from zenagent.cli.tui.screens.todo_list import TodoListScreen

    monkeypatch.setattr("zenagent.settings.get_pref", lambda name, default=None: None)
    app = ZenAgentApp(fake_runtime, language="zh")
    async with app.run_test() as pilot:
        await pilot.pause()
        approval = ToolApproval("bash", {"command": "ls"}, target="/tmp")
        app.push_screen(approval)
        await pilot.pause()
        assert "允许 bash" in _plain(approval.query_one("#dialog-title", Static))
        approval.dismiss(None)
        await pilot.pause()

        job = JobLogView(
            id="bg_1",
            command="sleep 1",
            status="running",
            duration_ms=1000,
            exit_code=None,
            output="hi",
        )
        job_screen = JobLogScreen(job)
        app.push_screen(job_screen)
        await pilot.pause()
        assert "job 日志" in _plain(job_screen.query_one("#dialog-title", Static))
        assert "运行中" in _plain(job_screen.query_one("#dialog-body", Static))
        job_screen.dismiss(None)
        await pilot.pause()

        report = ContextReport(
            slices=(), total_tokens=100, window=200, measured=True, prompt_tokens=100
        )
        ctx_screen = ContextBreakdownScreen(report)
        app.push_screen(ctx_screen)
        await pilot.pause()
        assert "上下文" in _plain(ctx_screen.query_one("#dialog-title", Static))
        ctx_screen.dismiss(None)
        await pilot.pause()

        todo_screen = TodoListScreen(
            [{"name": "阶段", "tasks": [{"content": "做点什么", "status": "blocked"}]}]
        )
        app.push_screen(todo_screen)
        await pilot.pause()
        assert "待办" in _plain(todo_screen.query_one("#dialog-title", Static))


async def test_app_chrome_and_help_are_chinese(fake_runtime, monkeypatch):
    from zenagent.cli.tui.app import ZenAgentApp
    from zenagent.cli.tui.widgets.prompt_input import PromptInput
    from zenagent.cli.tui.widgets.rows import NoticeRow
    from zenagent.cli.tui.widgets.transcript import TranscriptView

    monkeypatch.setattr("zenagent.settings.get_pref", lambda name, default=None: None)
    app = ZenAgentApp(fake_runtime, language="zh")
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.query_one(PromptInput).placeholder == "输入消息…"
        await app._dispatch_command("help", "")
        await pilot.pause()
        transcript = app.query_one(TranscriptView)
        notices = [row.plain for row in transcript.query(NoticeRow)]
        assert any("命令（输入 / 补全）" in line for line in notices)


def test_tool_card_statuses_are_chinese():
    from zenagent.app.views import ToolCallView
    from zenagent.cli.tui.widgets.tool_card import ToolCard

    i18n.set_locale("zh")
    try:
        view = ToolCallView(
            call_id="c1",
            name="bash",
            args_preview="",
            args={"command": "ls"},
            details={"exit_code": 0},
            status="ok",
        )
        plain = ToolCard(view)._renderable().plain
        assert "完成" in plain
        assert "退出码 0" in plain
    finally:
        i18n.set_locale("en")

