"""Textual themes built from the appearance tokens.

Textual needs one ``Theme`` object per appearance; the colours themselves stay
in :mod:`zenagent.cli.appearance`. Both themes are registered on the app before
its first paint, then ``App.theme`` selects the active one — Textual recomputes
every CSS variable from the theme, so the stylesheet follows for free.
"""

from __future__ import annotations

from textual.theme import Theme

from zenagent.cli.appearance import Appearance

__all__ = [
    "DARK_THEME_NAME",
    "LIGHT_THEME_NAME",
    "textual_theme",
    "theme_name",
]

#: Registered names (the ``harbor-`` prefix keeps clear of Textual's built-ins).
DARK_THEME_NAME = "harbor-dark"
LIGHT_THEME_NAME = "harbor-light"


def theme_name(appearance: Appearance) -> str:
    """The Textual theme name for ``appearance``."""
    return DARK_THEME_NAME if appearance.dark else LIGHT_THEME_NAME


def textual_theme(appearance: Appearance) -> Theme:
    """Translate appearance tokens into the Textual ``Theme`` contract.

    The semantic fields map onto the built-in CSS variables (``$background``,
    ``$surface``, ``$text``-family, ``$accent``, ``$error``, ``$success``,
    ``$warning``); every token is also exposed as a custom variable so the
    stylesheet can use ``$ink``, ``$line``, ``$tool-running-bg`` and friends.
    """
    tokens = appearance.tokens
    return Theme(
        name=theme_name(appearance),
        primary=tokens["accent"],
        secondary=tokens["sec-context"],
        warning=tokens["warn"],
        error=tokens["error"],
        success=tokens["ok"],
        accent=tokens["accent"],
        foreground=tokens["text"],
        background=tokens["ink"],
        surface=tokens["surface"],
        panel=tokens["surface"],
        dark=appearance.dark,
        variables=dict(tokens),
    )
