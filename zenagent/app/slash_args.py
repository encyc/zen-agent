"""Argument-value hints for slash commands, shared by every frontend.

Value sets are user-facing vocabulary: the REPL completes them, the TUI lists
them while typing, and the TUI uses the same table to populate its selectors.
Keeping one table here means those three paths cannot drift.

Commands whose values are dynamic (``/model``) are extended by their frontend;
this module only owns the fixed vocabulary.
"""

from __future__ import annotations

from zenagent.app.modes import MODES

__all__ = ["ARG_VALUES", "argument_values"]

#: Mode descriptions mirror ``describe_modes`` but stay picker-friendly.
_MODE_DESCRIPTIONS = {
    "plan": "read-only planning; proposes a plan",
    "ask": "confirm writes/commands (default)",
    "full": "run writes/commands automatically",
}

#: command (with slash) → [(value, one-line description), ...] in display order.
ARG_VALUES: dict[str, list[tuple[str, str]]] = {
    "/mode": [(mode, _MODE_DESCRIPTIONS[mode]) for mode in MODES],
    "/thinking": [
        ("off", "no reasoning"),
        ("low", "cheap / fast"),
        ("medium", "balanced"),
        ("high", "default for reasoning models"),
        ("max", "the model's supported maximum"),
    ],
    "/theme": [
        ("default", "built-in colours"),
        ("mono", "no colour"),
        ("solarized", "solarized palette"),
    ],
    "/mcp": [("retry", "reconnect failed servers")],
    "/images": [("clear", "forget attached images")],
}


def argument_values(command: str, prefix: str = "") -> list[tuple[str, str]]:
    """Values for ``command`` whose value starts with ``prefix`` (case-insensitive).

    ``command`` may be written with or without the leading slash.
    """
    key = command if command.startswith("/") else f"/{command}"
    values = ARG_VALUES.get(key.lower(), [])
    needle = prefix.strip().lower()
    return [
        (value, description)
        for value, description in values
        if value.lower().startswith(needle)
    ]
