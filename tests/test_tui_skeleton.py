"""TUI skeleton: mount, status bar and non-TTY fallback."""


from zenagent.cli.tui.app import ZenAgentApp
from zenagent.cli.tui.widgets.status_bar import StatusBar


async def test_app_mounts_and_shows_model_in_status_bar(fake_runtime):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        bar = app.query_one(StatusBar)
        assert fake_runtime.agent.model.id in bar.plain


def test_non_tty_falls_back_to_repl(monkeypatch):
    import zenagent.cli.main as cli_main
    import zenagent.cli.repl as repl_module

    called: dict = {}

    async def fake_repl(**kwargs):
        called.update(kwargs)
        return 0

    monkeypatch.setattr(repl_module, "repl", fake_repl)
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)

    assert cli_main.main([]) == 0
    assert called, "repl must run when stdout is not a tty"


async def test_screen_widgets_have_real_geometry(fake_runtime):
    """The app must paint widgets on the default screen, not inside a nested Screen."""
    from zenagent.cli.tui.widgets.chat_log import ChatLog

    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        log = app.query_one(ChatLog)
        assert log.region.width > 0 and log.region.height > 0


def test_missing_textual_gives_actionable_error(monkeypatch, capsys):
    import zenagent.cli.main as cli_main

    monkeypatch.setattr(
        cli_main,
        "_tui_app_class",
        lambda: (_ for _ in ()).throw(ImportError("no textual")),
    )
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    assert cli_main.main([]) == 2
    err = capsys.readouterr().err
    assert "extra cli" in err and "textual" in err
