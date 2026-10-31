"""Semantic colour tokens for the TUI, bound to the active appearance.

The values live in :mod:`zenagent.app.appearance` (the single colour source);
this module is the ergonomic accessor the widgets use. ``bind()`` swaps the
active appearance and every later read sees the new colours — which is why
widgets must read ``palette.TEXT`` at render time instead of snapshotting it
into a module constant.

Layout colours stay in ``theme.tcss`` (CSS cannot import Python) but come from
the same appearance through the Textual theme's CSS variables.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping

from zenagent.app.appearance import APPEARANCES, Appearance

__all__ = [
    "CONTEXT_COLORS",
    "SECTION_ACCENTS",
    "STATE_COLORS",
    "bind",
]

#: Accessor name → token name in the appearance.
_BASE = {
    "INK": "ink",
    "SURFACE": "surface",
    "LINE": "line",
    "TEXT": "text",
    "MUTED": "muted",
    "DIM": "dim",
    "ACCENT": "accent",
    "OK": "ok",
    "ERROR": "error",
    "WARN": "warn",
}

#: Bound appearance tokens; dark is the default so a bare import still works.
_active: Mapping[str, str] = APPEARANCES["dark"].tokens


def bind(appearance: Appearance) -> None:
    """Make ``appearance`` the source for every token read from now on."""
    global _active
    _active = appearance.tokens


def __getattr__(name: str) -> str:
    """Resolve ``palette.TEXT`` and friends against the active appearance."""
    token = _BASE.get(name)
    if token is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return _active[token]


def _section_accents() -> dict[str, str]:
    """One quiet accent per sidebar section so the panel scans by colour."""
    return {
        "session": _active["sec-session"],
        "model": _active["sec-model"],
        "runtime": _active["sec-runtime"],
        "context": _active["sec-context"],
        "todo": _active["accent"],
        "subagent": _active["sec-subagent"],
        "jobs": _active["sec-jobs"],
    }


def _state_colors() -> dict[str, str]:
    """Every state glyph the work panel renders, keyed by state name."""
    return {
        "running": _active["accent"],
        "in_progress": _active["accent"],
        "pending": _active["muted"],
        "ok": _active["ok"],
        "done": _active["ok"],
        "completed": _active["ok"],
        "abandoned": _active["dim"],
        "error": _active["error"],
        "failed": _active["error"],
        "blocked": _active["error"],
        "timed_out": _active["warn"],
        "aborted": _active["error"],
        "interrupted": _active["dim"],
        "truncated": _active["warn"],
    }


def _context_colors() -> dict[str, str]:
    """One colour per context slice (shares the section accents on purpose)."""
    accents = _section_accents()
    return {
        "system": accents["session"],
        "agents": accents["model"],
        "skills": accents["subagent"],
        "extensions": accents["runtime"],
        "plan": _active["accent"],
        "mode": _active["accent"],
        "tools": accents["jobs"],
        "session": _active["sec-context"],
        "other": _active["dim"],
    }


class _LiveMap(Mapping[str, str]):
    """A read-only mapping recomputed from the active appearance on every read."""

    def __init__(self, resolver: Callable[[], dict[str, str]]) -> None:
        self._resolver = resolver

    def __getitem__(self, key: str) -> str:
        return self._resolver()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._resolver())

    def __len__(self) -> int:
        return len(self._resolver())

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._resolver()!r})"


#: One quiet accent per sidebar section so the panel scans by colour.
SECTION_ACCENTS: Mapping[str, str] = _LiveMap(_section_accents)

#: State → colour; every glyph/status the work panel renders is covered.
STATE_COLORS: Mapping[str, str] = _LiveMap(_state_colors)

#: One colour per context slice (shares the section accents intentionally).
CONTEXT_COLORS: Mapping[str, str] = _LiveMap(_context_colors)
