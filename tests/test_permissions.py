"""Catastrophic-command policy: refused even under --yes, overridable explicitly."""

import pytest

from zenagent.app.permissions import (
    PermissionGate,
    build_permission_gate,
    dangerous_command_reason,
)
from zenagent.app.safety import GuardPolicy
from zenagent.types import ToolCall


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf /",
        "rm -rf /*",
        "sudo rm -fr ~",
        "rm -r -f /",
        "RM -RF /",
        ":(){ :|:& };:",
        "mkfs.ext4 /dev/sda1",
        "dd if=/dev/zero of=/dev/sda",
        "chmod -R 777 /",
        "shutdown -h now",
        "reboot",
        "echo x > /dev/sda",
        "init 0",
    ],
)
def test_catastrophic_commands_are_flagged(command):
    assert dangerous_command_reason(command) is not None


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf ./build",
        "rm -rf node_modules dist",
        "git push --force",
        "dd if=image.iso of=out.bin",
        "chmod -R 777 ./vendor",
        "echo hi > out.txt",
        "ls -la",
        "mkdocs build",
    ],
)
def test_normal_commands_pass(command):
    assert dangerous_command_reason(command) is None


def test_empty_command_is_not_dangerous():
    assert dangerous_command_reason("   ") is None


async def test_yes_does_not_disable_the_dangerous_command_policy():
    gate = PermissionGate(assume_yes=True, interactive=False)
    allowed, reason = await gate.check("bash", {"command": "rm -rf /"})
    assert allowed is False
    assert "refused" in reason and "--allow-dangerous" in reason


async def test_allow_dangerous_is_the_explicit_escape_hatch():
    gate = PermissionGate(assume_yes=True, interactive=False, allow_dangerous=True)
    assert (await gate.check("bash", {"command": "rm -rf /"}))[0] is True


async def test_safe_command_still_works_under_yes():
    gate = PermissionGate(assume_yes=True, interactive=False)
    assert (await gate.check("bash", {"command": "rm -rf ./build"}))[0] is True


async def test_hook_reports_the_refusal_to_the_model():
    gate = PermissionGate(assume_yes=True, interactive=False)
    call = ToolCall(id="c1", name="bash", arguments={"command": "mkfs.ext4 /dev/sda1"})
    verdict = await gate.before_tool_call(call, {"command": "mkfs.ext4 /dev/sda1"})
    assert verdict is not None and verdict["block"] is True
    assert "mkfs" in verdict["reason"]


async def test_non_bash_tools_are_unaffected():
    gate = PermissionGate(assume_yes=True, interactive=False)
    assert (await gate.check("write_file", {"path": "/tmp/x"}))[0] is True


async def test_loading_a_skill_is_read_only():
    """Reading a local SKILL.md must not require --yes."""
    gate = PermissionGate(assume_yes=False, interactive=False)
    allowed, reason = await gate.check("read_skill", {"name": "demo"})
    assert allowed is True, reason


# ---------------------------------------------------------------------------
# S2-T9: tool policy
# ---------------------------------------------------------------------------


async def test_deny_patterns_are_matched_with_fnmatch():
    gate = PermissionGate(assume_yes=True, deny=["write_*", "bash"])
    assert (await gate.check("write_file", {}))[0] is False
    assert (await gate.check("bash", {}))[0] is False
    assert (await gate.check("read_file", {}))[0] is True


async def test_allow_patterns_skip_confirmation():
    gate = PermissionGate(assume_yes=False, interactive=False, allow=["write_*"])
    allowed, reason = await gate.check("write_file", {"path": "x"})
    assert allowed is True, reason
    # tools outside the allowlist keep the normal non-interactive rule
    assert (await gate.check("bash", {"command": "ls"}))[0] is False


async def test_read_only_allows_reads_and_denies_everything_else():
    gate = PermissionGate(assume_yes=True, read_only=True)
    for name in ("read_file", "list_dir", "glob", "grep", "read_skill"):
        assert (await gate.check(name, {}))[0] is True, name
    for name in ("write_file", "edit_file", "bash", "mcp__files__write_file"):
        allowed, reason = await gate.check(name, {"command": "ls"})
        assert allowed is False and "read-only" in reason


async def test_allowlist_cannot_rescue_a_catastrophic_command():
    gate = PermissionGate(assume_yes=True, allow=["*"])
    allowed, reason = await gate.check("bash", {"command": "rm -rf /"})
    assert allowed is False and "--allow-dangerous" in reason


def test_matches_any_returns_the_pattern():
    from zenagent.app.permissions import matches_any

    assert matches_any(["read_*", "grep"], "read_file") == "read_*"
    assert matches_any(["write_*"], "read_file") is None


# ---------------------------------------------------------------------------
# P0-1: tool-declared approval tiers and the read-only classification
# ---------------------------------------------------------------------------


def _declared_tool(name: str, approval: str):
    from zenagent.agent.loop import AgentTool

    return AgentTool(
        name=name,
        description="declared tier",
        parameters={"type": "object", "properties": {}},
        approval=approval,
    )


async def test_web_tools_are_read_only_in_plan_mode():
    """Networking is a read: plan mode must allow web_search/fetch."""
    gate = PermissionGate(interactive=False, mode="plan")
    allowed, reason = await gate.check("web_search", {"query": "python 3.14"})
    assert allowed is True, reason
    allowed, reason = await gate.check("fetch", {"url": "https://example.com"})
    assert allowed is True, reason


async def test_web_tools_are_read_only_under_the_read_only_flag():
    gate = PermissionGate(interactive=False, read_only=True)
    assert (await gate.check("web_search", {"query": "x"}))[0] is True
    assert (await gate.check("fetch", {"url": "https://example.com"}))[0] is True


async def test_declared_read_only_tool_is_allowed_in_plan_mode():
    """A tool the kernel/extension declares read-only needs no name allowlist."""
    gate = PermissionGate(interactive=False, mode="plan")
    tool = _declared_tool("mcp__db__query", "read")
    allowed, reason = await gate.check("mcp__db__query", {"sql": "select 1"}, tool)
    assert allowed is True, reason


async def test_declared_tier_wins_over_the_name_allowlist():
    """The tool declaration is authoritative: an exec declaration is not a read."""
    gate = PermissionGate(interactive=False, mode="plan")
    tool = _declared_tool("read_file", "exec")
    allowed, reason = await gate.check("read_file", {"path": "x"}, tool)
    assert allowed is False
    assert "plan mode" in reason


async def test_declared_write_tool_needs_confirmation_in_ask_mode():
    gate = PermissionGate(interactive=False, mode="ask")
    tool = _declared_tool("mcp__db__write", "write")
    allowed, _ = await gate.check("mcp__db__write", {"sql": "update t"}, tool)
    assert allowed is False


async def test_declared_read_only_tool_is_allowed_under_the_read_only_flag():
    gate = PermissionGate(interactive=False, read_only=True)
    tool = _declared_tool("mcp__db__query", "read")
    assert (await gate.check("mcp__db__query", {}, tool))[0] is True


async def test_broker_honors_a_declared_read_only_tier():
    from zenagent.app.permissions import PermissionBroker

    broker = PermissionBroker.wrap(PermissionGate(interactive=False, mode="plan"))
    tool = _declared_tool("mcp__db__query", "read")
    call = ToolCall(id="c1", name="mcp__db__query", arguments={})
    assert await broker.before_tool_call(call, {}, tool) is None


async def test_every_builtin_tool_declares_an_approval_tier():
    from zenagent.tools import ALL_TOOLS

    tiers = {tool.name: tool.approval for tool in ALL_TOOLS}
    assert tiers["read_file"] == "read"
    assert tiers["list_dir"] == "read"
    assert tiers["glob"] == "read"
    assert tiers["grep"] == "read"
    assert tiers["read_skill" if "read_skill" in tiers else "web_search"] == "read"
    assert tiers["web_search"] == "read"
    # P0-1: fetch is the exfiltration channel, so it declares "ask" (2026-11-14 audit).
    assert tiers["fetch"] == "ask"
    assert tiers["write_file"] == "write"
    assert tiers["edit_file"] == "write"
    assert tiers["bash"] == "exec"
    assert set(tiers.values()) <= {"read", "ask", "write", "exec"}


async def test_plan_mode_gates_real_builtin_tools_by_their_declaration():
    from zenagent.tools import ALL_TOOLS

    gate = PermissionGate(interactive=False, mode="plan")
    for tool in ALL_TOOLS:
        allowed, reason = await gate.check(tool.name, {"command": "true"}, tool)
        assert allowed is (tool.approval == "read"), (tool.name, reason)


# ---------------------------------------------------------------------------
# P1-3: "always allow" is cached per target, not per tool class
# ---------------------------------------------------------------------------


async def test_always_allow_is_cached_per_path_not_per_tool(monkeypatch, tmp_path):
    import builtins

    answers = iter(["a", "n"])
    asked: list[str] = []

    def fake_input(prompt: str) -> str:
        asked.append(prompt)
        return next(answers)

    monkeypatch.setattr(builtins, "input", fake_input)
    gate = PermissionGate(interactive=True)
    target_a = str(tmp_path / "a.txt")
    target_b = str(tmp_path / "b.txt")

    assert (await gate.check("write_file", {"path": target_a}))[0] is True
    # Same target: remembered, no second prompt.
    assert (await gate.check("write_file", {"path": target_a}))[0] is True
    assert len(asked) == 1

    # Different target: asks again and can be denied.
    allowed, _reason = await gate.check("write_file", {"path": target_b})
    assert allowed is False
    assert len(asked) == 2  # a new target asks again
    assert "write_file(" in asked[1]


async def test_always_allow_for_bash_is_exact_commands(monkeypatch):
    """A bash grant covers the exact command, not a prefix family."""
    import builtins

    answers = iter(["a", "n"])
    asked: list[str] = []
    monkeypatch.setattr(
        builtins, "input", lambda prompt: (asked.append(prompt), next(answers))[1]
    )
    gate = PermissionGate(interactive=True)

    assert (await gate.check("bash", {"command": "git status"}))[0] is True
    # The exact same command is remembered.
    assert (await gate.check("bash", {"command": "git status"}))[0] is True
    assert len(asked) == 1
    # A variant is a different command and asks again.
    assert (await gate.check("bash", {"command": "git status --short"}))[0] is False
    assert len(asked) == 2


async def test_multiline_and_wrapper_bash_commands_are_not_prefix_shared(monkeypatch):
    """Consent scope must not leak across newline/wrapper variants."""
    import builtins

    answers = iter(["a", "n"])
    asked: list[str] = []
    monkeypatch.setattr(
        builtins, "input", lambda prompt: (asked.append(prompt), next(answers))[1]
    )
    gate = PermissionGate(interactive=True)

    first = "git status\nrm -rf ./x"
    assert (await gate.check("bash", {"command": first}))[0] is True
    # A different multi-line command must prompt again.
    assert (await gate.check("bash", {"command": "git status\nrm -rf ./y"}))[0] is False
    # A wrapper variant of a previously approved command must prompt too.
    assert len(asked) == 2
    monkeypatch.setattr(builtins, "input", lambda prompt: "n")
    assert (await gate.check("bash", {"command": "sudo git status"}))[0] is False


async def test_broker_always_allow_is_target_scoped():
    from zenagent.app.permissions import PermissionBroker

    prompts: list[tuple[str, dict]] = []

    async def confirm(name, args, verdict):
        prompts.append((name, dict(args)))
        return "always"

    broker = PermissionBroker.wrap(PermissionGate(interactive=True))
    broker.set_confirm(confirm)

    first = ToolCall(id="1", name="write_file", arguments={"path": "/tmp/x/one.txt"})
    assert await broker.before_tool_call(first, first.arguments) is None
    # Same path is remembered.
    assert await broker.before_tool_call(first, first.arguments) is None
    assert len(prompts) == 1
    # Different path prompts again (and here is approved).
    other = ToolCall(id="2", name="write_file", arguments={"path": "/tmp/x/two.txt"})
    assert await broker.before_tool_call(other, other.arguments) is None
    assert len(prompts) == 2


async def test_command_grant_does_not_cover_compound_commands(monkeypatch):
    """An exact grant for `git status` must not auto-allow `git status && ...`."""
    import builtins

    monkeypatch.setattr(builtins, "input", lambda prompt: "a")
    gate = PermissionGate(interactive=True)
    assert (await gate.check("bash", {"command": "git status"}))[0] is True

    monkeypatch.setattr(builtins, "input", lambda prompt: "n")
    allowed, _reason = await gate.check(
        "bash", {"command": "git status && rm -rf ./build"}
    )
    assert allowed is False


async def test_compound_command_grant_is_exact(monkeypatch):
    import builtins

    monkeypatch.setattr(builtins, "input", lambda prompt: "a")
    gate = PermissionGate(interactive=True)
    compound = "git status && touch marker"
    assert (await gate.check("bash", {"command": compound}))[0] is True
    assert (await gate.check("bash", {"command": compound}))[0] is True
    # A different compound command is not covered by the grant.
    monkeypatch.setattr(builtins, "input", lambda prompt: "n")
    assert (
        await gate.check("bash", {"command": "git status && touch other"})
    )[0] is False


async def test_blank_targets_never_fall_back_to_a_tool_wide_grant(monkeypatch):
    """A tool with a natural target must not become tool-wide on a blank arg."""
    import builtins

    answers = iter(["a", "n"])
    monkeypatch.setattr(builtins, "input", lambda prompt: next(answers))
    gate = PermissionGate(interactive=True)

    assert (await gate.check("write_file", {"path": ""}))[0] is True
    # The blank-target approval must not grant every later write.
    assert (await gate.check("write_file", {"path": "/tmp/other.txt"}))[0] is False


async def test_directory_grant_covers_one_level_but_not_descendants(monkeypatch, tmp_path):
    import builtins

    answers = iter(["a", "n"])
    asked: list[str] = []
    monkeypatch.setattr(
        builtins, "input", lambda prompt: (asked.append(prompt), next(answers))[1]
    )
    gate = PermissionGate(interactive=True)

    # Approve the directory itself (write_file's target is a path).
    assert (await gate.check("write_file", {"path": str(tmp_path)}))[0] is True
    assert len(asked) == 1
    # A direct child is covered by the one-level directory grant.
    assert (await gate.check("write_file", {"path": str(tmp_path / "child.txt")}))[0] is True
    assert len(asked) == 1
    # A nested descendant is not covered.
    nested = str(tmp_path / "sub" / "deep.txt")
    assert (await gate.check("write_file", {"path": nested}))[0] is False
    assert len(asked) == 2


async def test_directory_grant_does_not_leak_to_sibling_tools(monkeypatch, tmp_path):
    import builtins

    monkeypatch.setattr(builtins, "input", lambda prompt: "a")
    gate = PermissionGate(interactive=True)
    assert (await gate.check("write_file", {"path": str(tmp_path)}))[0] is True

    monkeypatch.setattr(builtins, "input", lambda prompt: "n")
    # Same directory, different tool: not covered.
    assert (await gate.check("edit_file", {"path": str(tmp_path / "x.py")}))[0] is False


async def test_task_and_ask_are_read_only_for_name_only_callers():
    from zenagent.tools.ask import build_ask_tool

    assert build_ask_tool(lambda: None).approval == "read"
    gate = PermissionGate(interactive=False, mode="plan")
    assert (await gate.check("ask", {}))[0] is True
    assert (await gate.check("task", {"prompt": "x"}))[0] is True
    # Declarations agree with the name fallback.
    async def _noop(*args, **kwargs):  # pragma: no cover - never executed
        raise AssertionError

    from zenagent.agent.loop import AgentTool

    task = AgentTool(
        name="task",
        description="task",
        parameters={"type": "object"},
        execute=_noop,
        approval="read",
    )
    assert (await gate.check("task", {"prompt": "x"}, task))[0] is True


# ---------------------------------------------------------------------------
# Risk verdicts: workspace fence, protected paths, destructive commands
# ---------------------------------------------------------------------------


async def test_outside_write_is_refused_with_flag_hint(tmp_path):
    gate = build_permission_gate(
        interactive=False, policy=GuardPolicy(workspace=tmp_path)
    )
    allowed, reason = await gate.check(
        "write_file", {"path": "/etc/hosts", "content": "x"}
    )
    assert allowed is False
    assert "--allow-outside" in reason


async def test_tools_allow_cannot_bypass_risk(tmp_path):
    gate = build_permission_gate(
        interactive=False,
        assume_yes=True,
        allow=["*"],
        policy=GuardPolicy(workspace=tmp_path),
    )
    assert (
        await gate.check("write_file", {"path": "/etc/hosts", "content": "x"})
    )[0] is False


async def test_allow_outside_policy_resolves_the_risk(tmp_path):
    workspace = tmp_path / "workspace"
    other = tmp_path / "other"
    workspace.mkdir()
    other.mkdir()
    gate = build_permission_gate(
        interactive=False,
        assume_yes=True,
        policy=GuardPolicy(workspace=workspace, allow_outside=(other,)),
    )
    assert (
        await gate.check("write_file", {"path": str(other / "x"), "content": "x"})
    )[0] is True


async def test_plain_workspace_write_still_allowed_under_yes(tmp_path):
    gate = build_permission_gate(
        interactive=False, assume_yes=True, policy=GuardPolicy(workspace=tmp_path)
    )
    assert (
        await gate.check("write_file", {"path": str(tmp_path / "a.py"), "content": "x"})
    )[0] is True


async def test_protected_path_requires_confirmation(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    gate = build_permission_gate(
        interactive=False, assume_yes=True, policy=GuardPolicy(workspace=tmp_path)
    )
    allowed, reason = await gate.check(
        "write_file", {"path": str(home / ".ssh" / "config"), "content": "x"}
    )
    assert allowed is False
    assert "--allow-protected" in reason


async def test_destructive_bash_requires_confirmation(tmp_path):
    gate = build_permission_gate(
        interactive=False,
        assume_yes=True,
        policy=GuardPolicy(workspace=tmp_path),
    )
    allowed, reason = await gate.check("bash", {"command": "git reset --hard"})
    assert allowed is False
    assert "--allow-destructive" in reason


async def test_destructive_bash_is_quiet_for_build_dir(tmp_path):
    gate = build_permission_gate(
        interactive=False,
        mode="auto",
        policy=GuardPolicy(workspace=tmp_path),
    )
    assert (await gate.check("bash", {"command": "rm -rf ./build"}))[0] is True


async def test_plan_mode_still_refuses_risky_writes(tmp_path):
    gate = build_permission_gate(
        interactive=False, mode="plan", policy=GuardPolicy(workspace=tmp_path)
    )
    allowed, reason = await gate.check(
        "write_file", {"path": "/etc/hosts", "content": "x"}
    )
    assert allowed is False
    assert "plan mode" in reason


async def test_catastrophic_bash_still_refused_under_yes(tmp_path):
    gate = build_permission_gate(
        interactive=False,
        assume_yes=True,
        policy=GuardPolicy(workspace=tmp_path),
    )
    allowed, reason = await gate.check("bash", {"command": "rm -rf /"})
    assert allowed is False
    assert "--allow-dangerous" in reason


async def test_read_tools_are_not_fenced(tmp_path):
    gate = build_permission_gate(
        interactive=False, policy=GuardPolicy(workspace=tmp_path)
    )
    assert (await gate.check("read_file", {"path": "/etc/hosts"}))[0] is True


async def test_interactive_risk_always_is_target_scoped(tmp_path, monkeypatch):
    prompts: list[str] = []
    answers = iter(["a"])

    def fake_input(prompt=""):
        prompts.append(prompt)
        return next(answers, "n")

    monkeypatch.setattr("builtins.input", fake_input)
    gate = build_permission_gate(
        interactive=True, policy=GuardPolicy(workspace=tmp_path)
    )
    args = {"path": "/etc/hosts", "content": "x"}
    assert (await gate.check("write_file", args))[0] is True
    assert (await gate.check("write_file", args))[0] is True
    assert len(prompts) == 1
    assert "outside workspace" in prompts[0]


async def test_assess_returns_verdict_kinds(tmp_path):
    gate = build_permission_gate(
        interactive=False, policy=GuardPolicy(workspace=tmp_path)
    )
    assert gate.assess("read_file", {"path": "x"}).kind == "allow"
    verdict = gate.assess("write_file", {"path": "/etc/hosts"})
    assert verdict.kind == "confirm" and verdict.risk == "outside"


def test_an_explicit_mode_switch_lifts_read_only():
    """``--read-only`` decides the startup mode; an in-session /mode overrides it."""
    from zenagent.app.permissions import PermissionGate

    gate = PermissionGate(mode="plan", read_only=True)
    assert gate.read_only is True

    gate.set_mode("auto")

    assert gate.read_only is False
    assert gate.mode == "auto"


async def test_read_protected_dir_requires_approval_even_in_auto():
    """P0-1: credential reads must surface an approval even in auto mode."""
    gate = PermissionGate(interactive=False, mode="auto")
    allowed, reason = await gate.check("read_file", {"path": "~/.zenagent/settings.json"})
    assert allowed is False
    assert "protected" in reason


async def test_read_project_file_still_free():
    """Ordinary project reads stay approval-free."""
    gate = PermissionGate(interactive=False, mode="auto")
    allowed, _ = await gate.check("read_file", {"path": "README.md"})
    assert allowed is True


async def test_grep_and_glob_over_protected_paths_require_approval_too():
    """The search tools read their whole ``path`` root — a grep over ~/.ssh
    reads every credential in it, so they sit behind the same gate as
    read_file (review finding: the exfil chain stayed open without this)."""
    gate = PermissionGate(interactive=False, mode="auto")
    for tool in ("grep", "glob"):
        allowed, reason = await gate.check(
            tool, {"path": "~/.zenagent/settings.json"}
        )
        assert allowed is False, tool
        assert "protected" in reason, tool


async def test_grep_in_the_workspace_stays_free():
    gate = PermissionGate(interactive=False, mode="auto")
    allowed, _ = await gate.check("grep", {"pattern": "x", "path": "."})
    assert allowed is True


async def test_fetch_requires_approval():
    """P0-1: fetch can exfiltrate anything that was read; it must not be free."""
    from zenagent.tools.web.tools import FETCH

    gate = PermissionGate(interactive=False)
    # The tool object rides along, as the loop passes it (tool_exec.py).
    allowed, reason = await gate.check("fetch", {"url": "https://example.com"}, FETCH)
    assert allowed is False
    assert reason
