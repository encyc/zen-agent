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
