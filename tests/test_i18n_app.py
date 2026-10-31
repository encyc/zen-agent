"""Shared app-layer chrome: slash args, mode summary and command descriptions."""

import pytest

from zenagent import i18n


@pytest.fixture(autouse=True)
def _restore_english():
    yield
    i18n.set_locale("en")


def test_slash_argument_descriptions_follow_the_locale():
    from zenagent.app.slash_args import argument_values

    assert ("plan", "read-only planning; proposes a plan") in argument_values("/mode")
    i18n.set_locale("zh")
    assert ("plan", "只读规划，给出计划") in argument_values("/mode")
    assert ("light", "harbor 亮色") in argument_values("/theme")


def test_describe_modes_is_localized():
    from zenagent.app.modes import describe_modes

    assert describe_modes().startswith("modes: plan =")
    i18n.set_locale("zh")
    assert describe_modes().startswith("模式：plan =")


def test_language_command_message_is_localized():
    from zenagent.app import commands

    i18n.set_locale("zh")
    try:
        assert commands.t("cmdmsg.lang_unknown", requested="x", choices="auto|en|zh") == (
            "未知语言：x（可选 auto|en|zh）"
        )
    finally:
        i18n.set_locale("en")


async def test_command_descriptions_and_notices_are_chinese(fake_runtime, monkeypatch):
    from zenagent.cli.tui.app import ZenAgentApp
    from zenagent.cli.tui.widgets.rows import NoticeRow
    from zenagent.cli.tui.widgets.transcript import TranscriptView

    monkeypatch.setattr("zenagent.settings.get_pref", lambda name, default=None: None)
    app = ZenAgentApp(fake_runtime, language="zh")
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.commands.get("help").description == "列出全部命令"
        assert app.commands.get("theme").description == "设置配色主题：auto|dark|light"
        await app._dispatch_command("images", "clear")
        await pilot.pause()
        transcript = app.query_one(TranscriptView)
        notices = [row.plain for row in transcript.query(NoticeRow)]
        assert any("已清空附加图片" in line for line in notices)
