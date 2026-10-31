"""Semantic colour tokens for the TUI.

Layout colours stay in ``theme.tcss`` (CSS cannot import Python), but every
widget that builds Rich text should take its colours from here instead of
repeating hex literals. The palette keeps the repo's quiet "harbor" base and
only adds distinction where it carries meaning: one accent per panel section
and one colour per state.

Token values are the same ones the stylesheet was written with, so existing
screens keep their look; new widgets inherit it for free.
"""

from __future__ import annotations

__all__ = [
    "ACCENT",
    "CONTEXT_COLORS",
    "DIM",
    "ERROR",
    "INK",
    "LINE",
    "MUTED",
    "OK",
    "SECTION_ACCENTS",
    "STATE_COLORS",
    "SURFACE",
    "TEXT",
]

#: Page background.
INK = "#0d1319"
#: Raised surfaces (sidebar, dock strips, dialogs).
SURFACE = "#141c24"
#: Hairlines and borders.
LINE = "#263441"
#: Primary text.
TEXT = "#d7e0e8"
#: Secondary text.
MUTED = "#8296a6"
#: Tertiary text (hints, timestamps).
DIM = "#5d6f7e"
#: The single warm accent: activity, current item, focus.
ACCENT = "#d9a86c"
#: Success / additions.
OK = "#8fbf8f"
#: Failures.
ERROR = "#e08787"

#: One quiet accent per sidebar section so the panel scans by colour.
SECTION_ACCENTS: dict[str, str] = {
    "Session": "#7fb3c9",  # cool blue
    "Model": "#9ab87a",  # moss
    "Runtime": "#c9a97f",  # sand
    "Context": "#7f9ec9",  # horizon blue
    "Todo": ACCENT,  # warm
    "Subagent": "#b79ad0",  # lavender
    "Jobs": "#7fb398",  # sea green
}

#: State → colour; every glyph/status the work panel renders is covered.
STATE_COLORS: dict[str, str] = {
    "running": ACCENT,
    "in_progress": ACCENT,
    "pending": MUTED,
    "ok": OK,
    "done": OK,
    "completed": OK,
    "abandoned": DIM,
    "error": ERROR,
    "failed": ERROR,
    "blocked": ERROR,
    "timed_out": "#d9c06c",
    "aborted": ERROR,
    "interrupted": DIM,
    "truncated": "#d9c06c",
}

#: One colour per context slice (shares the section accents intentionally).
CONTEXT_COLORS: dict[str, str] = {
    "system": SECTION_ACCENTS["Session"],
    "agents": SECTION_ACCENTS["Model"],
    "skills": SECTION_ACCENTS["Subagent"],
    "extensions": SECTION_ACCENTS["Runtime"],
    "plan": ACCENT,
    "mode": ACCENT,
    "tools": SECTION_ACCENTS["Jobs"],
    "session": "#7f9ec9",
    "other": DIM,
}
