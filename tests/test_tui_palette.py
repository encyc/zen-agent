"""Semantic palette: one place for widget colours, bound to the appearance.

Layout colours stay in ``theme.tcss``; widgets render Rich text in Python, so
the same tokens live once here — but values come from the active appearance and
must be read at render time, never snapshotted at import.
"""

from zenagent.cli.appearance import APPEARANCES
from zenagent.cli.tui import palette

SECTIONS = ["Session", "Model", "Runtime", "Context", "Todo", "Subagent", "Jobs"]


def test_bind_switches_palette_tokens():
    palette.bind(APPEARANCES["dark"])
    assert palette.TEXT == "#d7e0e8"
    palette.bind(APPEARANCES["light"])
    assert palette.TEXT == "#1f262c"
    assert palette.ACCENT == "#8a5a20"
    assert palette.SECTION_ACCENTS["Session"] == "#2a6478"
    assert palette.STATE_COLORS["timed_out"] == "#7a5c1a"
    palette.bind(APPEARANCES["dark"])


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


def test_state_and_context_maps_track_the_binding():
    palette.bind(APPEARANCES["light"])
    assert palette.CONTEXT_COLORS["system"] == palette.SECTION_ACCENTS["Session"]
    assert palette.CONTEXT_COLORS["session"] == palette.SECTION_ACCENTS["Context"]
    assert palette.STATE_COLORS["pending"] == palette.MUTED
    palette.bind(APPEARANCES["dark"])
    assert palette.CONTEXT_COLORS["system"] == "#7fb3c9"


def test_unknown_attribute_raises():
    import pytest

    with pytest.raises(AttributeError):
        _ = palette.NOT_A_COLOUR


async def test_sidebar_titles_use_their_section_accent(fake_runtime):
    from zenagent.cli.tui.app import ZenAgentApp
    from zenagent.cli.tui.widgets.sidebar import Sidebar, SidebarSection

    palette.bind(APPEARANCES["dark"])
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
            expected = palette.SECTION_ACCENTS[section.title].lower()
            assert color.hex6.lower() == expected, section.title
