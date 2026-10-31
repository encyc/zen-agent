"""Every TUI colour must come from the theme, never from a literal hex.

The appearance module owns the values; the stylesheet and widget-local CSS read
them through Textual's theme variables. This test is the guardrail that keeps a
new widget from quietly re-introducing a hardcoded colour (and thus a light
theme that only half works).
"""

import re
from pathlib import Path

TUI = Path(__file__).resolve().parents[1] / "zenagent" / "cli" / "tui"
HEX = re.compile(r"#[0-9a-fA-F]{6}")
# Rich text styles cannot resolve CSS variables; those call sites must use palette.
RICH_CSS_VAR = re.compile(r'style=f?"\$[a-z][a-z-]*"')


def test_no_hardcoded_hex_in_tui_styles():
    offenders = []
    for path in sorted([*TUI.rglob("*.py"), *TUI.rglob("*.tcss")]):
        if "__pycache__" in path.parts:
            continue
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if HEX.search(line):
                offenders.append(f"{path.relative_to(TUI)}:{number}: {line.strip()}")
    assert not offenders, "\n".join(offenders)


def test_rich_styles_do_not_use_css_variables():
    offenders = []
    for path in sorted(TUI.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if RICH_CSS_VAR.search(line):
                offenders.append(f"{path.relative_to(TUI)}:{number}: {line.strip()}")
    assert not offenders, "\n".join(offenders)


async def test_theme_variables_resolve_to_tokens(fake_runtime):
    from zenagent.app.appearance import APPEARANCES
    from zenagent.cli.tui.app import ZenAgentApp

    app = ZenAgentApp(fake_runtime, theme="light")
    async with app.run_test() as pilot:
        await pilot.pause()
        tokens = APPEARANCES["light"].tokens
        assert app.theme_variables["ink"] == tokens["ink"]
        assert app.theme_variables["surface"] == tokens["surface"]
        assert app.theme_variables["tool-running-bg"] == tokens["tool-running-bg"]
        assert app.theme_variables["sec-session"] == tokens["sec-session"]


async def test_stylesheet_renders_in_both_appearances(fake_runtime):
    """Both themes must parse and paint: a bad variable would fail the boot."""
    from zenagent.cli.tui.app import ZenAgentApp

    for value, name in (("dark", "harbor-dark"), ("light", "harbor-light")):
        app = ZenAgentApp(fake_runtime, theme=value)
        try:
            async with app.run_test() as pilot:
                await pilot.pause()
                assert app.theme == name
                assert app.screen.styles.background is not None
        finally:
            from zenagent.app.appearance import APPEARANCES
            from zenagent.cli.tui import palette

            palette.bind(APPEARANCES["dark"])
