"""Semantic palette: one place for widget colours, one accent per section.

Layout colours stay in ``theme.tcss``; widgets render Rich text in Python, so
the same tokens live once here instead of a hardcoded hex per widget.
"""

import re

from zenagent.cli.tui import palette

_HEX = re.compile(r"#[0-9a-f]{6}$")

SECTIONS = ["Session", "Model", "Runtime", "Context", "Todo", "Subagent", "Jobs"]

def test_palette_colours_are_hex():
    for name in ("INK", "SURFACE", "LINE", "TEXT", "MUTED", "DIM", "ACCENT", "OK", "ERROR"):
        value = getattr(palette, name)
        assert _HEX.fullmatch(value), (name, value)


def test_section_accents_cover_the_panel_sections():
    assert list(palette.SECTION_ACCENTS) == SECTIONS


def test_state_colours_cover_every_rendered_state():
    states = {
        "running",
        "ok",
        "error",
        "failed",
        "timed_out",
        "pending",
        "in_progress",
        "done",
        "blocked",
        "aborted",
        "interrupted",
        "truncated",
    }
    assert states <= set(palette.STATE_COLORS)


def _rgb_hex(value: str) -> str:
    return value.lower()


async def test_sidebar_titles_use_their_section_accent(fake_runtime):
    from zenagent.cli.tui.app import ZenAgentApp
    from zenagent.cli.tui.widgets.sidebar import Sidebar, SidebarSection

    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+b")
        await pilot.pause()

        sidebar = app.query_one(Sidebar)
        for section in sidebar.query(SidebarSection):
            title = section.query_one(".sidebar-title")
            color = title.styles.color
            assert color is not None, section.title
            expected = _rgb_hex(palette.SECTION_ACCENTS[section.title])
            assert color.hex6.lower() == expected, section.title
