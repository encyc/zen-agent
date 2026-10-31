"""Render-time labels for app-layer reports.

Reports (context composition, todo phases, job status) carry stable ids and
English labels because the app layer is UI-agnostic. The TUI translates the ids
here, at paint time, so a language switch only has to repaint — the app never
has to know which locale is active.
"""

from __future__ import annotations

from zenagent.i18n import t

__all__ = ["context_label"]

#: Context slice id → catalog key (kept in sync with ``app/context.py``).
_CONTEXT_KEYS: dict[str, str] = {
    "system": "tui.context.system",
    "agents": "tui.context.agents",
    "skills": "tui.context.skills",
    "extensions": "tui.context.extensions",
    "plan": "tui.context.plan",
    "mode": "tui.context.mode",
    "tools": "tui.context.tools",
    "session": "tui.context.session",
    "other": "tui.context.other",
}


def context_label(slice_id: str, fallback: str) -> str:
    """Translated label for a context slice, falling back to the report's text."""
    key = _CONTEXT_KEYS.get(slice_id)
    if key is None:
        return fallback
    translated = t(key)
    return fallback if translated == key else translated
