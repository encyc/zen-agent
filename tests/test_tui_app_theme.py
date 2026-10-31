"""TUI appearance wiring: two Textual themes, palette bound at startup."""

from zenagent.cli.appearance import APPEARANCES
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
