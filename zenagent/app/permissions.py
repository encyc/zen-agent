"""Harness-side permission gate wired as the loop's ``before_tool_call``.

The kernel has no permission concept on purpose (pi's philosophy): confirmation
is the harness's job. This gate classifies tools and asks the user only for the
classes that can change the machine.

Reading tools pass straight through; write/execute tools require either zen
mode or ``--yes``, a previous "always allow" answer for that tool in this
session, or an interactive confirmation. In a non-interactive run without
``--yes`` they are denied with an explanatory reason, which the model sees as
an is_error result.

``zen`` is the top rung: even the confirmable risk tiers (destructive command,
protected path, outside workspace) run instead of asking. Policy — catastrophic
commands, explicit deny patterns, plan mode — still refuses.
"""

from __future__ import annotations

import asyncio
import fnmatch
import re
from collections.abc import Sequence
from pathlib import Path

from zenagent.app.modes import DEFAULT_MODE, normalize_mode
from zenagent.app.safety import (
    GuardPolicy,
    Verdict,
    classify_write_path,
    default_allow_outside,
    destructive_command_reason,
    workspace_root,
)
from zenagent.i18n import t
from zenagent.types import ToolCall

__all__ = [
    "READ_ONLY_TOOLS",
    "PermissionGate",
    "build_permission_gate",
    "dangerous_command_reason",
    "matches_any",
    "render_approval_prompt",
    "tool_is_read_only",
    "tool_target",
]

#: Fallback classification for callers that only have a tool name (tests, the
#: `check(name, args)` shorthand). Tools normally declare their own tier via
#: ``AgentTool.approval`` and that declaration is authoritative.
#:
#: ``read_skill`` reads a local markdown file, and ``web_search``/``fetch``
#: only read remote state, so they belong here even though they are not
#: filesystem tools.
READ_ONLY_TOOLS = frozenset(
    {
        "read_file",
        "list_dir",
        "glob",
        "grep",
        "web_search",
        "fetch",
        "read_skill",
        "wait",
        "ask",
        "task",
    }
)

#: Cap for argument text echoed in a prompt.
_MAX_ARGS_PREVIEW = 120

#: Tools whose path becomes the risk target (and the session grant key).
_PATH_WRITE_TOOLS = frozenset({"write_file", "edit_file"})

#: How a non-interactive run can explicitly resolve each confirm-level risk.
_RISK_HINTS = {
    "outside": "pass --allow-outside PATH to allow it",
    "protected": "pass --allow-protected to allow it",
    "destructive": "pass --allow-destructive to allow it",
}

#: Paths whose recursive forced deletion is never a normal coding action.
_CATASTROPHIC_TARGETS = frozenset({"/", "/*", "~", "~/", "$home", "*", "/.*", "/*/"})

#: Commands that take the machine down regardless of arguments.
_DISRUPTIVE_COMMANDS = frozenset({"shutdown", "reboot", "halt", "poweroff"})


def matches_any(patterns: list[str], name: str) -> str | None:
    """Return the first fnmatch pattern matching ``name`` (or None)."""
    for pattern in patterns:
        if fnmatch.fnmatchcase(name, pattern):
            return pattern
    return None


#: Tools whose approval is scoped to a concrete filesystem path.
_PATH_TARGET_TOOLS = frozenset({"read_file", "write_file", "edit_file", "list_dir"})


def tool_target(tool_name: str, args: dict) -> str | None:
    """Stable target key for a session-scoped "always allow" grant.

    A grant is scoped to what the call touches, not to the tool class: one
    ``write_file`` approval must not silently allow every later write. File
    tools key on the resolved path; bash keys on the first two command words
    (``git status`` covers ``git status --short`` but not ``git push``).
    Tools with no natural target return ``None`` and fall back to a
    tool-wide grant.
    """
    if not isinstance(args, dict):
        return None
    if tool_name in _PATH_TARGET_TOOLS:
        raw = args.get("path")
        if not raw:
            return None
        try:
            resolved = Path(str(raw)).expanduser().resolve()
        except (OSError, RuntimeError):
            resolved = Path(str(raw))
        kind = "dir" if resolved.is_dir() else "file"
        return f"{tool_name}:{kind}:{resolved}"
    if tool_name == "bash":
        # Exact command only. Prefix grants for shell commands are a consent
        # trap: `git status`, `git status --short`, `git status && rm -rf x`
        # and multi-line scripts all share the same first words.
        command = str(args.get("command") or "").strip()
        if not command:
            return None
        return f"bash:{command}"
    return None


def target_is_granted(tool_name: str, args: dict, grants: set[str]) -> bool:
    """Whether a call is covered by an already-remembered target grant.

    Exact targets cover themselves. A directory grant additionally covers the
    *direct children* of that directory for the same tool (one level only, no
    descendants, no cross-tool leakage).
    """
    target = tool_target(tool_name, args)
    if target is None:
        return False
    if target in grants:
        return True
    marker = ":file:"
    if marker in target:
        tool_part, path = target.split(marker, 1)
        parent = f"{tool_part}:dir:{Path(path).parent}"
        if parent in grants:
            return True
    return False


def expects_target(tool_name: str) -> bool:
    """Whether the tool always has a natural approval target."""
    return tool_name in _PATH_TARGET_TOOLS or tool_name == "bash"


def tool_is_read_only(tool_name: str, tool=None) -> bool:
    """Whether a call only reads state and never needs confirmation.

    A tool's own ``approval`` declaration wins; the ``READ_ONLY_TOOLS`` name
    set is only the fallback for callers that don't pass the tool object
    (direct ``check(name, args)`` calls and tests).
    """
    approval = getattr(tool, "approval", None)
    if approval is not None:
        return approval == "read"
    return tool_name in READ_ONLY_TOOLS


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


def render_approval_prompt(
    tool_name: str, args: dict, verdict: Verdict | None = None
) -> str:
    """The text prompt for one tool approval.

    Shared by ``PermissionGate._ask`` and the REPL's broker confirm callback so
    both interactive surfaces word the question — risk line, legible target and
    answer options — identically.
    """
    target = tool_target(tool_name, args)
    if target is not None:
        # Show what the grant will cover (path / command prefix), not just
        # the argument blob: a one-time path approval must be legible.
        preview = target.split(":", 1)[1]
    else:
        preview = ", ".join(f"{key}={value!r}" for key, value in args.items())
    if len(preview) > _MAX_ARGS_PREVIEW:
        preview = preview[:_MAX_ARGS_PREVIEW] + "…"
    risk_line = ""
    if verdict is not None and verdict.risk is not None:
        if verdict.risk == "destructive":
            risk_line = t("perm.risk_destructive", reason=verdict.reason) + "\n"
        else:
            risk_line = t(f"perm.risk_{verdict.risk}", target=preview) + "\n"
    return risk_line + t("perm.repl_prompt", tool=tool_name, args=preview)


class PermissionGate:
    """Decide whether a tool call may run, asking the user when needed."""

    def __init__(
        self,
        assume_yes: bool = False,
        interactive: bool = False,
        allow_dangerous: bool = False,
        allow: list[str] | None = None,
        deny: list[str] | None = None,
        read_only: bool = False,
        mode: str = DEFAULT_MODE,
        policy: GuardPolicy | None = None,
    ) -> None:
        self.assume_yes = assume_yes
        self.interactive = interactive
        self.allow_dangerous = allow_dangerous
        self.allow = list(allow or [])
        self.deny = list(deny or [])
        self.read_only = read_only
        self.mode = self._validated_mode(mode)
        self.policy = policy or GuardPolicy(
            workspace=workspace_root(), allow_outside=default_allow_outside()
        )
        #: Tools the user answered "a" for during this session.
        self._always_allowed: set[str] = set()
        #: Confirm-level risk targets the user explicitly allowed this session.
        self._risk_grants: set[str] = set()

    @staticmethod
    def _validated_mode(mode: str) -> str:
        normalized = normalize_mode(mode)
        if normalized is None:
            raise ValueError(t("mode.unknown", mode=mode))
        return normalized

    def set_mode(self, mode: str) -> None:
        """Switch the runtime mode; the next tool call sees it immediately."""
        self.mode = self._validated_mode(mode)

    def assess(self, tool_name: str, args: dict, tool=None) -> Verdict:
        """Classify a call as allow, confirm (with a risk label) or refuse.

        The order is the design's fixed precedence: catastrophic command, deny
        pattern, plan/read-only policy, then the new risk tiers, then allow.
        """
        read_only = tool_is_read_only(tool_name, tool)
        if tool_name == "bash" and not self.allow_dangerous:
            # --yes means "don't ask me", not "run anything": catastrophic
            # commands need the explicit --allow-dangerous escape hatch.
            danger = dangerous_command_reason(str(args.get("command", "")))
            if danger is not None:
                return Verdict(
                    "refuse", f"refused {danger}; this needs --allow-dangerous"
                )
        denied_by = matches_any(self.deny, tool_name)
        if denied_by is not None:
            return Verdict(
                "refuse", f"tool {tool_name!r} is denied by policy ({denied_by})"
            )
        if self.mode == "plan" and not read_only:
            return Verdict(
                "refuse",
                f"tool {tool_name!r} is blocked by plan mode (read-only); "
                "switch to /mode ask or /mode zen to execute",
            )
        if self.read_only and not read_only:
            return Verdict("refuse", f"tool {tool_name!r} is blocked by --read-only")
        # zen is the "never ask" rung: the three confirmable risk tiers below
        # run instead of asking. Everything above is policy and still wins.
        confirm_risks = self.mode != "zen"
        if confirm_risks and tool_name == "bash" and not self.allow_dangerous:
            # --allow-dangerous is the single escape hatch for catastrophic
            # *and* destructive shell commands; asking for it twice would be
            # a trap for scripted runs.
            reason = destructive_command_reason(
                str(args.get("command", "")), self.policy
            )
            if reason is not None:
                return Verdict(
                    "confirm",
                    f"destructive command ({reason})",
                    risk="destructive",
                    target=tool_target(tool_name, args),
                )
        if tool_name in _PATH_WRITE_TOOLS:
            raw = args.get("path")
            if isinstance(raw, str) and raw:
                try:
                    scope, resolved = classify_write_path(raw, self.policy)
                except (OSError, RuntimeError) as exc:
                    return Verdict("refuse", f"cannot resolve {raw!r}: {exc}")
                if confirm_risks and scope == "protected":
                    return Verdict(
                        "confirm",
                        f"{resolved} is a protected credential path",
                        risk="protected",
                        target=tool_target(tool_name, args),
                    )
                if confirm_risks and scope == "outside":
                    return Verdict(
                        "confirm",
                        f"{resolved} is outside the workspace "
                        f"{self.policy.workspace}",
                        risk="outside",
                        target=tool_target(tool_name, args),
                    )
        return Verdict("allow", "")

    def policy_refusal(self, tool_name: str, args: dict, tool=None) -> str | None:
        """Why a call is refused *before any confirmation*, or ``None``.

        Danger, explicit deny patterns, plan mode and ``--read-only`` are
        policy: they beat both ``--yes`` and a session-wide "always allow".
        """
        verdict = self.assess(tool_name, args, tool)
        return verdict.reason if verdict.kind == "refuse" else None

    async def check(self, tool_name: str, args: dict, tool=None) -> tuple[bool, str]:
        """Return ``(allowed, reason)``; reason is empty when allowed."""
        verdict = self.assess(tool_name, args, tool)
        if verdict.kind == "refuse":
            return False, verdict.reason
        if verdict.kind == "confirm":
            target = verdict.target or tool_target(tool_name, args)
            if target is not None and target in self._risk_grants:
                return True, ""
            if self.interactive:
                return self._ask(tool_name, args, verdict)
            hint = _RISK_HINTS.get(verdict.risk or "", "")
            return False, f"{verdict.reason}; {hint}" if hint else verdict.reason
        if matches_any(self.allow, tool_name) is not None:
            return True, ""
        if tool_is_read_only(tool_name, tool):
            return True, ""
        if target_is_granted(tool_name, args, self._always_allowed):
            return True, ""
        if (
            self.mode == "zen"
            or self.assume_yes
            or tool_name in self._always_allowed
        ):
            return True, ""
        if not self.interactive:
            reason = (
                f"permission denied for {tool_name!r}: this is a "
                "non-interactive run without --yes"
            )
            return False, reason
        return self._ask(tool_name, args)

    def _ask(
        self, tool_name: str, args: dict, verdict: Verdict | None = None
    ) -> tuple[bool, str]:
        target = tool_target(tool_name, args)
        try:
            answer = input(
                render_approval_prompt(tool_name, args, verdict)
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False, f"permission denied for {tool_name!r}: no answer"
        if answer in ("a", "all"):
            if verdict is not None and verdict.kind == "confirm":
                if target is not None:
                    self._risk_grants.add(target)
                return True, ""
            if target is not None:
                self._always_allowed.add(target)
            elif not expects_target(tool_name):
                # Target-less tools fall back to a tool-wide grant; a tool with
                # a natural target but a blank argument must not.
                self._always_allowed.add(tool_name)
            return True, ""
        if answer in ("y", "yes"):
            return True, ""
        return False, f"permission denied for {tool_name!r} by the user"

    async def before_tool_call(
        self, tool_call: ToolCall | str, args: dict, tool=None
    ) -> dict | None:
        """Loop hook: ``{"block": True, "reason": ...}`` or ``None`` to allow.

        Accepts either a :class:`ToolCall` (the loop's hook contract) or a bare
        tool name, so tests and callers can reuse it directly. ``tool`` is the
        resolved tool object when the loop calls the hook.
        """
        tool_name = tool_call.name if isinstance(tool_call, ToolCall) else str(tool_call)
        allowed, reason = await self.check(tool_name, args, tool)
        if allowed:
            return None
        return {"block": True, "reason": reason}


def build_permission_gate(
    *,
    interactive: bool,
    assume_yes: bool = False,
    allow_dangerous: bool = False,
    allow: list[str] | None = None,
    deny: list[str] | None = None,
    mode: str = DEFAULT_MODE,
    read_only: bool = False,
    policy: GuardPolicy | None = None,
) -> PermissionGate:
    """Build the gate every frontend uses, so policy wiring never drifts."""
    return PermissionGate(
        assume_yes=assume_yes,
        interactive=interactive,
        allow_dangerous=allow_dangerous,
        allow=allow,
        deny=deny,
        read_only=read_only,
        mode=mode,
        policy=policy,
    )


def build_broker_for(
    *,
    user_config,
    mode: str,
    read_only: bool = False,
    assume_yes: bool = False,
    allow_dangerous: bool = False,
    allow_outside: Sequence[str] = (),
    allow_destructive: bool = False,
    allow_protected: bool = False,
    cwd: Path | None = None,
) -> PermissionBroker:
    """Build the policy + gate + broker every frontend needs, in one place.

    Policy wiring used to be copied into each frontend, which is how the RPC
    ended up being the only one that forwarded ``read_only``. Frontends now
    supply only what is genuinely theirs: the mode, the CLI flags and (later)
    the confirm callback.
    """
    policy = GuardPolicy.from_config(
        user_config,
        cwd=cwd or Path.cwd(),
        allow_outside=list(allow_outside),
        allow_destructive=allow_destructive,
        allow_protected=allow_protected,
    )
    gate = build_permission_gate(
        assume_yes=assume_yes,
        interactive=False,
        allow_dangerous=allow_dangerous,
        allow=user_config.tool_allow,
        deny=user_config.tool_deny,
        mode=mode,
        read_only=read_only,
        policy=policy,
    )
    return PermissionBroker.wrap(gate)


class PermissionBroker:
    """Interactive approval on top of PermissionGate.

    ``deny_all()`` is the headless/TUI fallback: reads pass, everything else is
    refused with a reason the model can see.
    """

    def __init__(self, gate=None) -> None:
        self._gate = gate or PermissionGate(assume_yes=False, interactive=False)
        self._confirm = None
        self._allowed: set[str] = set()
        #: Target-scoped "always" grants (paths / command prefixes).
        self._allowed_targets: set[str] = set()
        #: Targets a confirm-level risk was explicitly allowed for this session.
        self._risk_targets: set[str] = set()
        # Tool calls run concurrently; approval modals must not stack.
        self._confirm_lock = asyncio.Lock()

    @classmethod
    def deny_all(cls) -> PermissionBroker:
        return cls()

    @classmethod
    def wrap(cls, gate) -> PermissionBroker:
        return cls(gate)

    @property
    def mode(self) -> str:
        return self._gate.mode

    def set_mode(self, mode: str) -> None:
        self._gate.set_mode(mode)

    def set_confirm(self, callback) -> None:
        self._confirm = callback

    def allow_for_session(self, tool_name: str) -> None:
        self._allowed.add(tool_name)

    async def before_tool_call(self, tool_call, arguments: dict, tool=None):
        name = (
            tool_call.name if isinstance(tool_call, ToolCall) else str(tool_call)
        )
        target = tool_target(name, arguments)

        # Policy first: plan mode, deny patterns and catastrophic commands win
        # over a session-wide "always allow" grant.
        verdict = self._gate.assess(name, arguments, tool)
        if verdict.kind == "refuse":
            return {"block": True, "reason": verdict.reason}
        if verdict.kind == "confirm":
            # Only ask mode reaches here: zen already downgraded these tiers to
            # allow inside assess(), and the refuse tiers returned above. Risky
            # actions still confirm; ordinary ones were already allowed.
            risk_target = verdict.target or target
            if risk_target is not None and risk_target in self._risk_targets:
                return None
            if self._confirm is None:
                return {"block": True, "reason": verdict.reason}
            async with self._confirm_lock:
                answer = await self._confirm(name, arguments, verdict)
            if answer is True or answer == "allow":
                return None
            if answer == "always":
                if risk_target is not None:
                    self._risk_targets.add(risk_target)
                return None
            return {"block": True, "reason": f"denied by user: {name}"}
        if target is not None and target_is_granted(name, arguments, self._allowed_targets):
            return None
        if tool_is_read_only(name, tool) or name in self._allowed:
            return None
        if matches_any(self._gate.allow, name) is not None:
            return None
        if self._gate.mode == "zen":
            return None
        if self._confirm is None:
            return {"block": True, "reason": f"tool {name!r} requires approval"}
        async with self._confirm_lock:
            answer = await self._confirm(name, arguments, verdict)
        if answer is True or answer == "allow":
            return None
        if answer == "always":
            if target is not None:
                self._allowed_targets.add(target)
            elif not expects_target(name):
                self.allow_for_session(name)
            return None
        return {"block": True, "reason": f"denied by user: {name}"}
