"""Argument-value hints for slash commands, shared by every frontend.

Value sets are user-facing vocabulary: the REPL completes them, the TUI lists
them while typing, and the TUI uses the same table to populate its selectors.
Keeping one table here means those three paths cannot drift.

Commands whose values are dynamic (``/model``) are extended by their frontend;
this module only owns the fixed vocabulary.
"""

from __future__ import annotations

from zenagent.app.modes import MODES
from zenagent.i18n import t

__all__ = ["ARG_VALUES", "argument_values"]

#: Mode descriptions mirror ``describe_modes`` but stay picker-friendly.
#: command (with slash) → [(value, catalog key of the one-line description), ...]
#: in display order. Keys are resolved at read time, so a language switch is
#: picked up by the next picker open / completion request.
ARG_VALUES: dict[str, list[tuple[str, str]]] = {
    "/mode": [(mode, f"arg.mode.{mode}") for mode in MODES],
    "/thinking": [
        ("off", "arg.thinking.off"),
        ("low", "arg.thinking.low"),
        ("medium", "arg.thinking.medium"),
        ("high", "arg.thinking.high"),
        ("max", "arg.thinking.max"),
    ],
    "/theme": [
        ("auto", "arg.theme.auto"),
        ("dark", "arg.theme.dark"),
        ("light", "arg.theme.light"),
    ],
    "/lang": [
        ("auto", "arg.lang.auto"),
        ("en", "arg.lang.en"),
        ("zh", "arg.lang.zh"),
    ],
    "/mcp": [("retry", "arg.mcp.retry")],
    "/images": [("clear", "arg.images.clear")],
}


def argument_values(command: str, prefix: str = "") -> list[tuple[str, str]]:
    """Values for ``command`` whose value starts with ``prefix`` (case-insensitive).

    ``command`` may be written with or without the leading slash.
    """
    key = command if command.startswith("/") else f"/{command}"
    values = ARG_VALUES.get(key.lower(), [])
    needle = prefix.strip().lower()
    return [
        (value, t(description))
        for value, description in values
        if value.lower().startswith(needle)
    ]
