"""Colour appearances: the single token source for every ZenAgent frontend.

The TUI draws its Rich text from :mod:`zenagent.cli.tui.palette`, its stylesheet
from CSS variables fed by a Textual theme, and the REPL derives its Rich styles
from the same seven semantic fields. All of them read *these* tokens, so a
light/dark switch cannot drift between the two frontends.

Two appearances ship: ``dark`` is the existing "harbor" palette (cold slate with
a warm accent); ``light`` is the same design on warm paper. Values were checked
for contrast: body/status colours stay above 4.5:1 on their surface, the
tertiary ``dim`` hint above 3:1.

This module is deliberately UI-free (no textual, no rich): only stdlib types.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

__all__ = [
    "APPEARANCES",
    "THEME_CHOICES",
    "Appearance",
    "detect_dark",
    "resolve_theme",
]

#: Page background, raised surfaces, hairlines, text tiers and semantic accents.
DARK_TOKENS: dict[str, str] = {
    "ink": "#0d1319",
    "surface": "#141c24",
    "raised": "#1b222a",
    "line": "#263441",
    "text": "#d7e0e8",
    "muted": "#8296a6",
    "dim": "#5d6f7e",
    "accent": "#d9a86c",
    "ok": "#8fbf8f",
    "error": "#e08787",
    "warn": "#d9c06c",
    "tool-running-bg": "#121a22",
    "tool-ok-bg": "#10171e",
    "tool-error-bg": "#241a1a",
    "error-banner-bg": "#3a1d1f",
    "sec-session": "#7fb3c9",
    "sec-model": "#9ab87a",
    "sec-runtime": "#c9a97f",
    "sec-context": "#7f9ec9",
    "sec-subagent": "#b79ad0",
    "sec-jobs": "#7fb398",
}

#: The same design on paper (warm off-white), contrast-checked against
#: ``surface`` for every text/status token.
LIGHT_TOKENS: dict[str, str] = {
    "ink": "#f7f5f0",
    "surface": "#efece4",
    "raised": "#f0ece3",
    "line": "#d8d3c8",
    "text": "#1f262c",
    "muted": "#55616a",
    "dim": "#6b747c",
    "accent": "#8a5a20",
    "ok": "#356b40",
    "error": "#a63e36",
    "warn": "#7a5c1a",
    "tool-running-bg": "#eaf1f4",
    "tool-ok-bg": "#ecf1e8",
    "tool-error-bg": "#f7e8e5",
    "error-banner-bg": "#f6dcda",
    "sec-session": "#2a6478",
    "sec-model": "#566f39",
    "sec-runtime": "#7d5f28",
    "sec-context": "#39657f",
    "sec-subagent": "#67527f",
    "sec-jobs": "#396f61",
}


@dataclass(frozen=True, slots=True)
class Appearance:
    """One resolved colour appearance (dark or light)."""

    name: str
    dark: bool
    tokens: Mapping[str, str]


APPEARANCES: dict[str, Appearance] = {
    "dark": Appearance("dark", True, DARK_TOKENS),
    "light": Appearance("light", False, LIGHT_TOKENS),
}

#: The user-facing vocabulary of the ``theme`` setting and ``--theme`` flag.
THEME_CHOICES: tuple[str, ...] = ("auto", "dark", "light")


def _environ(environ: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if environ is None else environ


def detect_dark(environ: Mapping[str, str] | None = None) -> bool:
    """Is the terminal on a dark background?

    ``COLORFGBG`` is the portable hint terminals set (``"15;0"`` means a bright
    foreground on a black background); its last field is the background index.
    Everything else — missing, malformed, or only a foreground index — falls
    back to dark, which is the shipped default.
    """
    raw = _environ(environ).get("COLORFGBG", "").strip()
    if not raw:
        return True
    background = raw.split(";")[-1].strip()
    try:
        index = int(background)
    except ValueError:
        return True
    return index < 8


def _resolve(spec: str, environ: Mapping[str, str] | None) -> Appearance | None:
    """Resolve one already-stripped spec, or return ``None`` when unknown."""
    if spec == "auto":
        return APPEARANCES["dark" if detect_dark(environ) else "light"]
    return APPEARANCES.get(spec)


def resolve_theme(
    flag: str | None = None,
    *,
    pref: str | None = None,
    environ: Mapping[str, str] | None = None,
    warnings: list[str] | None = None,
) -> Appearance:
    """Pick the appearance: explicit flag, then the saved preference, then auto.

    Unknown values never fail the run: they warn once and fall through to the
    next source (finally ``auto``), because a bad colour name must not stop a
    session from starting.
    """
    for source in (flag, pref):
        spec = (source or "").strip().lower()
        if not spec:
            continue
        resolved = _resolve(spec, environ)
        if resolved is not None:
            return resolved
        if warnings is not None:
            warnings.append(
                f"unknown theme {spec!r} (choose auto|dark|light); using auto"
            )
    return APPEARANCES["dark" if detect_dark(environ) else "light"]
