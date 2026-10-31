"""TUI appearance wiring: two Textual themes, palette bound at startup."""

from zenagent.app.appearance import APPEARANCES
from zenagent.cli.tui import palette


def test_textual_theme_carries_fields_and_variables():
    from zenagent.cli.tui.app_theme import textual_theme

    dark = textual_theme(APPEARANCES["dark"])
    assert dark.name == "harbor-dark"
    assert dark.dark is True
    assert dark.background == "#0d1319"
    assert dark.foreground == "#d7e0e8"
    assert dark.variables["ink"] == "#0d1319"
    assert dark.variables["tool-running-bg"] == "#121a22"
    assert dark.variables["line"] == "#263441"

    light = textual_theme(APPEARANCES["light"])
    assert light.name == "harbor-light"
    assert light.dark is False
    assert light.background == "#f7f5f0"
    assert light.variables["ink"] == "#f7f5f0"


async def test_app_starts_with_the_configured_theme(fake_runtime):
    from zenagent.cli.tui.app import ZenAgentApp

    app = ZenAgentApp(fake_runtime, theme="light")
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.theme == "harbor-light"
            assert palette.INK == "#f7f5f0"
            assert app.theme_variables["ink"] == "#f7f5f0"
    finally:
        palette.bind(APPEARANCES["dark"])


async def test_app_defaults_to_dark_without_a_preference(fake_runtime, monkeypatch):
    from zenagent.cli.tui.app import ZenAgentApp

    monkeypatch.setattr("zenagent.settings.get_pref", lambda name, default=None: None)
    app = ZenAgentApp(fake_runtime)
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.theme == "harbor-dark"
    finally:
        palette.bind(APPEARANCES["dark"])


async def test_saved_light_preference_is_used(fake_runtime, monkeypatch):
    from zenagent.cli.tui.app import ZenAgentApp

    monkeypatch.setattr(
        "zenagent.settings.get_pref", lambda name, default=None: "light"
    )
    app = ZenAgentApp(fake_runtime)
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.theme == "harbor-light"
    finally:
        palette.bind(APPEARANCES["dark"])


async def test_unknown_theme_warns_and_boots_dark(fake_runtime):
    from zenagent.cli.tui.app import ZenAgentApp

    app = ZenAgentApp(fake_runtime, theme="nonsense")
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.theme == "harbor-dark"
            assert app._appearance_warnings == []
    finally:
        palette.bind(APPEARANCES["dark"])


async def test_theme_command_switches_persists_and_repaints(fake_runtime, monkeypatch):
    from zenagent.cli.tui.app import ZenAgentApp
    from zenagent.cli.tui.widgets.transcript import TranscriptView

    saved: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "zenagent.settings.set_pref",
        lambda name, value: saved.append((name, value)),
    )
    app = ZenAgentApp(fake_runtime)
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            transcript = app.query_one(TranscriptView)
            resets: list[int] = []
            original_reset = transcript.reset
            monkeypatch.setattr(
                transcript, "reset", lambda: (resets.append(1), original_reset())[1]
            )
            await app._dispatch_command("theme", "light")
            await pilot.pause()
            assert app.theme == "harbor-light"
            assert palette.INK == "#f7f5f0"
            assert resets, "switching must rebuild the rich transcript"
            assert ("theme", "light") in saved
    finally:
        palette.bind(APPEARANCES["dark"])


async def test_theme_command_rejects_unknown_value(fake_runtime):
    from zenagent.cli.tui.app import ZenAgentApp

    app = ZenAgentApp(fake_runtime)
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            await app._dispatch_command("theme", "nonsense")
            await pilot.pause()
            assert app.theme == "harbor-dark"
            assert app._theme_spec == "auto"
    finally:
        palette.bind(APPEARANCES["dark"])


async def test_theme_command_auto_follows_the_terminal(fake_runtime, monkeypatch):
    from zenagent.cli.tui.app import ZenAgentApp

    monkeypatch.setenv("COLORFGBG", "0;15")
    app = ZenAgentApp(fake_runtime, theme="dark")
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            await app._dispatch_command("theme", "auto")
            await pilot.pause()
            assert app.theme == "harbor-light"
    finally:
        palette.bind(APPEARANCES["dark"])


async def test_theme_command_without_argument_opens_the_picker(fake_runtime):
    from zenagent.cli.tui.app import ZenAgentApp

    app = ZenAgentApp(fake_runtime)
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            await app._dispatch_command("theme", "")
            await pilot.pause()
            assert "theme" in app._menus
    finally:
        palette.bind(APPEARANCES["dark"])


def test_theme_slash_values_are_the_new_vocabulary():
    from zenagent.app.slash_args import argument_values

    values = [value for value, _ in argument_values("/theme")]
    assert values == ["auto", "dark", "light"]
