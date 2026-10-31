"""Context section and breakdown overlay: measured total, estimated slices."""

from zenagent.app.context import ContextReport, ContextSlice
from zenagent.app.views import RunStatus
from zenagent.cli.tui.app import ZenAgentApp
from zenagent.cli.tui.screens.context_breakdown import ContextBreakdownScreen
from zenagent.cli.tui.widgets.prompt_input import PromptInput
from zenagent.cli.tui.widgets.sidebar import SidebarRow, SidebarSection


def _report(maxed: bool = True) -> ContextReport:
    slices = [
        ContextSlice("system", "system prompt", 1200, 300, 15.0),
        ContextSlice("agents", "AGENTS.md", 2400, 600, 30.0),
        ContextSlice("skills", "skills", 400, 100, 5.0),
        ContextSlice("tools", "tool schemas", 0, 500, 25.0),
        ContextSlice("session", "session", 0, 500, 25.0),
    ]
    return ContextReport(
        slices=tuple(slices),
        total_tokens=2000,
        window=8000,
        measured=maxed,
        prompt_tokens=2000,
    )


def _status(report: ContextReport | None, *, used: int = 2000) -> RunStatus:
    return RunStatus(
        model_id="fake-model",
        thinking=None,
        running=False,
        context_used=used,
        context_window=8000,
        context=report,
    )


def _install_status(monkeypatch, status: RunStatus) -> None:
    monkeypatch.setattr(
        "zenagent.app.chat.ChatController.status", property(lambda _self: status)
    )


def _section(app: ZenAgentApp) -> SidebarSection:
    for section in app.query_one("Sidebar").query(SidebarSection):
        if section.title == "Context":
            return section
    raise AssertionError("no Context section")


async def _open(app, pilot) -> None:
    await pilot.press("ctrl+b")
    await pilot.pause()


async def test_sidebar_context_section_shows_total_bar_and_biggest_slices(
    fake_runtime, monkeypatch
):
    _install_status(monkeypatch, _status(_report()))
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await _open(app, pilot)

        section = _section(app)
        assert section.display is True
        rows = [row.plain for row in section.query(SidebarRow)]
        assert "ctx 25%" in rows[0]
        assert "2k/8k" in rows[0]
        assert "█" in rows[1]
        assert "%" in rows[2]


async def test_context_section_shows_zero_before_the_first_turn(fake_runtime):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await _open(app, pilot)

        section = _section(app)
        assert section.display is True
        assert "ctx 0%" in section.query(SidebarRow)[0].plain


async def test_clicking_the_context_row_opens_the_breakdown(
    fake_runtime, monkeypatch
):
    _install_status(monkeypatch, _status(_report()))
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await _open(app, pilot)

        row = next(row for row in _section(app).query(SidebarRow) if row.key == "ctx-total")
        await pilot.click(row)
        await pilot.pause()

        assert isinstance(app.screen, ContextBreakdownScreen)


async def test_breakdown_lists_slices_with_exact_chars_and_estimated_tokens(
    fake_runtime, monkeypatch
):
    _install_status(monkeypatch, _status(_report()))
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        app.action_show_context_breakdown()
        await pilot.pause()

        text = " ".join(
            str(widget.content) for widget in app.screen.query(".context-row")
        )
        assert "AGENTS.md" in text
        assert "≈600" in text
        assert "2400" in text, "characters are exact"
        assert "2k" in text or "2,000" in text

        footer = " ".join(
            str(widget.content) for widget in app.screen.query(".context-note")
        )
        assert "estimate" in footer.lower()


async def test_breakdown_marks_an_unmeasured_total(fake_runtime, monkeypatch):
    _install_status(monkeypatch, _status(_report(maxed=False)))
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        app.action_show_context_breakdown()
        await pilot.pause()

        footer = " ".join(
            str(widget.content) for widget in app.screen.query(".context-note")
        )
        assert "estimated" in footer.lower()


async def test_esc_closes_the_breakdown_and_restores_the_prompt(
    fake_runtime, monkeypatch
):
    _install_status(monkeypatch, _status(_report()))
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        app.action_show_context_breakdown()
        await pilot.pause()

        await pilot.press("escape")
        await pilot.pause()

        assert not isinstance(app.screen, ContextBreakdownScreen)
        assert app.focused is app.query_one(PromptInput)
