"""Configurable keybindings for the REPL prompt.

Only the bindings that are safe to override are exposed; everything else keeps
prompt_toolkit's defaults. A broken file falls back to the defaults.
"""

from __future__ import annotations

import json
from pathlib import Path

from prompt_toolkit.key_binding import KeyBindings

__all__ = [
    "DEFAULT_KEYBINDINGS",
    "build_key_bindings",
    "keybindings_path",
    "load_keybindings",
]

#: Action -> key name (prompt_toolkit syntax).
#:
#: ``newline`` deliberately uses Alt+Enter rather than ``c-j``: Ctrl-J is the
#: LF character, which is also what a piped newline looks like, so binding it
#: would swallow "submit" input in scripts.
DEFAULT_KEYBINDINGS: dict[str, str] = {
    "newline": "escape,enter",
    "submit": "enter",
    "abort": "c-c",
}

_ACTIONS = frozenset(DEFAULT_KEYBINDINGS)


def keybindings_path() -> Path:
    return Path.home() / ".zenagent" / "keybindings.json"


def load_keybindings(
    path: Path | None = None, warnings: list[str] | None = None
) -> dict[str, str]:
    """Read the binding map; missing/invalid files fall back to the defaults."""
    bindings = dict(DEFAULT_KEYBINDINGS)
    target = Path(path) if path is not None else keybindings_path()
    if not target.is_file():
        return bindings
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(  # noqa: TRY004 - config errors are ValueError by contract
                "keybindings file must contain an object"
            )
        for action, key in data.items():
            if action not in _ACTIONS:
                raise ValueError(
                f"unknown action {action!r}"
            )
            if not isinstance(key, str) or not key:
                raise ValueError(
                    f"action {action!r} needs a key string"
                )
            bindings[action] = key
    except ValueError as exc:
        if warnings is not None:
            warnings.append(f"{target}: {exc}; using default keybindings")
        return dict(DEFAULT_KEYBINDINGS)
    return bindings


def build_key_bindings(bindings: dict[str, str] | None = None) -> KeyBindings:
    """Build the prompt_toolkit bindings for the configurable actions."""
    config = {**DEFAULT_KEYBINDINGS, **(bindings or {})}
    key_bindings = KeyBindings()
    try:
        newline_key = config["newline"]
        if not newline_key:
            return key_bindings

        @key_bindings.add(newline_key)
        def _insert_newline(event) -> None:
            event.app.current_buffer.insert_text("\n")
    except ValueError:  # invalid key syntax in a user config: keep defaults
        return KeyBindings()

    # submit/abort keep prompt_toolkit's defaults; the config still documents
    # them so users know what they are.
    return key_bindings
