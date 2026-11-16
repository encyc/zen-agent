"""When a frontend may run a slash command relative to a live turn.

The classic REPL keeps reading while the model streams — that is how steering
works — so a slash command can be typed ahead of the run. Commands that rewrite
the transcript, the session file or the agent's toolset refuse mid-run
("cannot reload while the agent is working", "nothing to compact"), which loses
the line the user typed. Frontends therefore defer these until the turn
settles and run everything else immediately (help, mode, theme, quit).
"""

from __future__ import annotations

__all__ = ["DEFERRED_COMMANDS"]

#: Slash-command names that must act on a finished turn instead of a live one.
DEFERRED_COMMANDS = frozenset(
    {
        "clear",
        "compact",
        "export",
        "fork",
        "fresh",
        "handoff",
        "import",
        "model",
        "new",
        "plan-review",
        "reload",
        "restore",
        "retry",
        "undo",
    }
)
