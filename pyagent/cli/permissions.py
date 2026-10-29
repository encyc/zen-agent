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

from pyagent.types import ToolCall

__all__ = ["READ_ONLY_TOOLS", "PermissionGate"]

#: Tools that only read state; they never need confirmation.
READ_ONLY_TOOLS = frozenset({"read_file", "list_dir", "glob", "grep"})

#: Cap for argument text echoed in a prompt.
_MAX_ARGS_PREVIEW = 120


class PermissionGate:
    """Decide whether a tool call may run, asking the user when needed."""

    def __init__(self, assume_yes: bool = False, interactive: bool = False) -> None:
        self.assume_yes = assume_yes
        self.interactive = interactive
        #: Tools the user answered "a" for during this session.
        self._always_allowed: set[str] = set()

    async def check(self, tool_name: str, args: dict) -> tuple[bool, str]:
        """Return ``(allowed, reason)``; reason is empty when allowed."""
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
