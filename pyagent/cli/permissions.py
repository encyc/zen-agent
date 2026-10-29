"""Harness-side permission gate wired as the loop's ``before_tool_call``.

The kernel has no permission concept on purpose (pi's philosophy): confirmation
is the harness's job. This gate classifies tools and asks the user only for the
classes that can change the machine.

Reading tools pass straight through; write/execute tools require either
``--yes``, a previous "always allow" answer for that tool in this session, or an
interactive confirmation. In a non-interactive run without ``--yes`` they are
denied with an explanatory reason, which the model sees as an is_error result.
"""

from __future__ import annotations

import re

from pyagent.types import ToolCall

__all__ = ["READ_ONLY_TOOLS", "PermissionGate", "dangerous_command_reason"]

#: Tools that only read state; they never need confirmation. ``read_skill``
#: reads a local markdown file, so it belongs here even though it is not a
#: filesystem tool.
READ_ONLY_TOOLS = frozenset({"read_file", "list_dir", "glob", "grep", "read_skill"})

#: Cap for argument text echoed in a prompt.
_MAX_ARGS_PREVIEW = 120

#: Paths whose recursive forced deletion is never a normal coding action.
_CATASTROPHIC_TARGETS = frozenset({"/", "/*", "~", "~/", "$home", "*", "/.*", "/*/"})

#: Commands that take the machine down regardless of arguments.
_DISRUPTIVE_COMMANDS = frozenset({"shutdown", "reboot", "halt", "poweroff"})


def _dangerous_rm(command: str) -> str | None:
    """Recursive + forced ``rm`` aimed at a root/home/wildcard path."""
    for segment in re.split(r"[;&|\n]", command):
        tokens = segment.split()
        if not tokens:
            continue
        if tokens[0] == "sudo":
            tokens = tokens[1:]
        if not tokens or tokens[0] != "rm":
            continue
        flags = [t for t in tokens[1:] if t.startswith("-")]
        targets = [t for t in tokens[1:] if not t.startswith("-")]
        recursive = any("r" in f or f == "--recursive" for f in flags)
        force = any("f" in f or f == "--force" for f in flags)
        if recursive and force:
            for target in targets:
                normalized = target.lower().rstrip("/")
                if target.lower() in _CATASTROPHIC_TARGETS or normalized in _CATASTROPHIC_TARGETS:
                    return f"recursive forced delete of {target!r}"
    return None


#: (predicate/regex, human reason). Kept small and explicit on purpose:
#: anything a normal coding task needs must never match.
def dangerous_command_reason(command: str) -> str | None:
    """Return why ``command`` is catastrophic, or ``None`` when it looks normal.

    Normalisation (lowercase, collapsed whitespace) keeps the patterns readable
    and avoids case tricks; patterns are intentionally narrow so everyday
    commands (``rm -rf ./build``, ``git push``, ``dd if=image of=card.bin``)
    pass straight through.
    """
    normalized = " ".join(command.lower().split())
    if not normalized:
        return None

    reason = _dangerous_rm(normalized)
    if reason:
        return reason
    if re.search(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;?\s*:", normalized):
        return "fork bomb"
    if re.search(r"\bmkfs(\.\w+)?\b", normalized):
        return "filesystem format (mkfs)"
    if re.search(r"\bdd\b[^;&|]*\bof=/dev/", normalized):
        return "raw write to a block device (dd of=/dev/...)"
    if re.search(r"\bchmod\b[^;&|]*-[a-z]*r[a-z]*\s+777\s+/", normalized):
        return "chmod -R 777 /"
    if re.search(r">\s*/dev/(sd|disk|nvme)", normalized):
        return "overwrite a block device via redirection"
    first = normalized.split()[0].removeprefix("sudo ").split()[0]
    if first in _DISRUPTIVE_COMMANDS:
        return f"system power command ({first})"
    if re.search(r"\b(init|telinit)\s+[06]\b", normalized):
        return "system power command (init 0/6)"
    return None


class PermissionGate:
    """Decide whether a tool call may run, asking the user when needed."""

    def __init__(
        self,
        assume_yes: bool = False,
        interactive: bool = False,
        allow_dangerous: bool = False,
    ) -> None:
        self.assume_yes = assume_yes
        self.interactive = interactive
        self.allow_dangerous = allow_dangerous
        #: Tools the user answered "a" for during this session.
        self._always_allowed: set[str] = set()

    async def check(self, tool_name: str, args: dict) -> tuple[bool, str]:
        """Return ``(allowed, reason)``; reason is empty when allowed."""
        if tool_name == "bash" and not self.allow_dangerous:
            # --yes means "don't ask me", not "run anything": catastrophic
            # commands need the explicit --allow-dangerous escape hatch.
            danger = dangerous_command_reason(str(args.get("command", "")))
            if danger is not None:
                return (
                    False,
                    f"refused {danger}; this needs --allow-dangerous",
                )
        if tool_name in READ_ONLY_TOOLS:
            return True, ""
        if self.assume_yes or tool_name in self._always_allowed:
            return True, ""
        if not self.interactive:
            reason = (
                f"permission denied for {tool_name!r}: this is a "
                "non-interactive run without --yes"
            )
            return False, reason
        return self._ask(tool_name, args)

    def _ask(self, tool_name: str, args: dict) -> tuple[bool, str]:
        preview = ", ".join(f"{key}={value!r}" for key, value in args.items())
        if len(preview) > _MAX_ARGS_PREVIEW:
            preview = preview[:_MAX_ARGS_PREVIEW] + "…"
        try:
            answer = input(f"allow {tool_name}({preview})? [y/n/a] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False, f"permission denied for {tool_name!r}: no answer"
        if answer in ("a", "all"):
            self._always_allowed.add(tool_name)
            return True, ""
        if answer in ("y", "yes"):
            return True, ""
        return False, f"permission denied for {tool_name!r} by the user"

    async def before_tool_call(self, tool_call: ToolCall | str, args: dict) -> dict | None:
        """Loop hook: ``{"block": True, "reason": ...}`` or ``None`` to allow.

        Accepts either a :class:`ToolCall` (the loop's hook contract) or a bare
        tool name, so tests and callers can reuse it directly.
        """
        tool_name = tool_call.name if isinstance(tool_call, ToolCall) else str(tool_call)
        allowed, reason = await self.check(tool_name, args)
        if allowed:
            return None
        return {"block": True, "reason": reason}
