"""Every ``CommandResult.screen`` literal the app layer can emit has an opener.

The TUI routes command results through ``SCREEN_OPENERS``; a screen name added
to ``app/commands.py`` without a registry entry would silently do nothing, so
both sides are pinned: the registry covers exactly the literals in the source,
and the known key set survives refactors of either file.
"""

from __future__ import annotations

import re
from pathlib import Path

from zenagent.cli.tui.app import ZenAgentApp

COMMANDS_SRC = (
    Path(__file__).parents[1] / "zenagent" / "app" / "commands.py"
).read_text(encoding="utf-8")


def test_every_screen_literal_in_commands_is_registered():
    literals = set(re.findall(r'screen="([a-z-]+)"', COMMANDS_SRC))
    assert literals == set(ZenAgentApp.SCREEN_OPENERS)


def test_known_screen_keys_are_pinned():
    assert {
        "model-picker",
        "session-picker",
        "session-picker-all",
        "mode-picker",
        "thinking-picker",
        "theme-picker",
        "language-picker",
        "settings",
        "plan-review",
        "display-reset",
        "hotkeys",
        "usage-dashboard",
        "mcp-add",
        "todo-edit",
        "search-settings",
    } <= set(ZenAgentApp.SCREEN_OPENERS)
