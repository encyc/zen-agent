"""plan/ask/zen runtime modes: resolution, gate behavior, prompt note."""

import pytest

from zenagent.app.modes import (
    DEFAULT_MODE,
    MODES,
    describe_modes,
    mode_prompt_note,
    normalize_mode,
    resolve_mode,
)
from zenagent.app.permissions import PermissionGate


def test_modes_are_plan_ask_zen():
    assert MODES == ("plan", "ask", "zen")
    assert DEFAULT_MODE == "ask"


@pytest.mark.parametrize("raw", ["plan", "PLAN", " Ask ", "zen"])
def test_normalize_mode_accepts_case_and_space(raw):
    assert normalize_mode(raw) in MODES


@pytest.mark.parametrize("raw", ["yolo", "full", "", None])
def test_normalize_mode_rejects_unknown(raw):
    assert normalize_mode(raw) is None


def test_explicit_mode_wins_over_flags():
    assert resolve_mode(explicit="zen", assume_yes=False, read_only=True) == "zen"


def test_yes_maps_to_zen():
    assert resolve_mode(explicit=None, assume_yes=True) == "zen"


def test_read_only_maps_to_plan():
    assert resolve_mode(explicit=None, read_only=True) == "plan"


def test_default_mode_is_ask():
    assert resolve_mode(explicit=None) == "ask"


def test_invalid_explicit_mode_raises():
    with pytest.raises(ValueError):
        resolve_mode(explicit="bogus")


@pytest.mark.parametrize("mode", MODES)
def test_gate_accepts_each_mode(mode):
    assert PermissionGate(mode=mode, interactive=False).mode == mode


def test_gate_rejects_unknown_mode():
    with pytest.raises(ValueError):
        PermissionGate(mode="bogus")


async def test_plan_denies_mutating_tools_even_with_yes():
    gate = PermissionGate(mode="plan", assume_yes=True, interactive=False)
    blocked, reason = await gate.check("write_file", {"path": "x", "content": "y"})
    assert blocked is False and "plan" in reason.lower()
    blocked, _ = await gate.check("bash", {"command": "ls"})
    assert blocked is False


async def test_plan_allows_read_only_tools():
    gate = PermissionGate(mode="plan", interactive=False)
    for name in ("read_file", "grep", "glob", "list_dir"):
        allowed, reason = await gate.check(name, {})
        assert allowed is True, name
        assert reason == ""


async def test_ask_still_denies_in_non_interactive_runs():
    gate = PermissionGate(mode="ask", interactive=False)
    allowed, reason = await gate.check("write_file", {"path": "x"})
    assert allowed is False and "non-interactive" in reason


# ---------------------------------------------------------------------------
# zen: the no-confirmation rung. Risk tiers run instead of asking; policy wins.
# ---------------------------------------------------------------------------


async def test_zen_allows_write_without_yes():
    gate = PermissionGate(mode="zen", interactive=False)
    allowed, reason = await gate.check("write_file", {"path": "x"})
    assert allowed is True and reason == ""


async def test_zen_runs_every_risk_tier_instead_of_asking(tmp_path):
    from pathlib import Path

    from zenagent.app.safety import GuardPolicy

    gate = PermissionGate(
        mode="zen", interactive=False, policy=GuardPolicy(workspace=tmp_path)
    )
    for name, args in (
        ("bash", {"command": "git reset --hard"}),  # destructive
        ("write_file", {"path": str(Path.home() / ".npmrc")}),  # protected
        ("write_file", {"path": "/etc/hosts"}),  # outside
    ):
        allowed, reason = await gate.check(name, args)
        assert allowed is True, (name, reason)


async def test_zen_still_refuses_catastrophic_commands():
    gate = PermissionGate(mode="zen", allow_dangerous=False)
    allowed, reason = await gate.check("bash", {"command": "rm -rf /"})
    assert allowed is False and "--allow-dangerous" in reason


async def test_deny_pattern_still_beats_zen():
    gate = PermissionGate(mode="zen", deny=["write_file"])
    allowed, reason = await gate.check("write_file", {"path": "x"})
    assert allowed is False and "denied by policy" in reason


def test_set_mode_switches_gate():
    gate = PermissionGate(mode="ask", interactive=False)
    gate.set_mode("zen")
    assert gate.mode == "zen"


def test_set_mode_rejects_unknown():
    with pytest.raises(ValueError):
        PermissionGate().set_mode("bogus")


def test_plan_prompt_note_is_explicit_about_read_only():
    note = mode_prompt_note("plan")
    assert "PLAN" in note
    assert "read-only" in note.lower()


def test_zen_prompt_note_tells_the_model_to_act():
    note = mode_prompt_note("zen")
    assert "ZEN" in note
    assert "without asking" in note


def test_ask_prompt_note_is_empty():
    assert mode_prompt_note("ask") == ""


def test_describe_modes_lists_all_modes():
    text = describe_modes()
    for mode in MODES:
        assert mode in text


async def test_runtime_starts_in_ask_and_exposes_mode(runtime_factory):
    runtime = await runtime_factory()
    assert runtime.mode == "ask"


async def test_runtime_options_can_select_zen(runtime_factory):
    runtime = await runtime_factory(mode="zen")
    assert runtime.mode == "zen"
    assert runtime.gate.mode == "zen"
    assert "ZEN MODE" in runtime.agent.system_prompt


async def test_runtime_set_mode_updates_gate_and_status(runtime_factory):
    from zenagent.app.chat import ChatController

    runtime = await runtime_factory()
    chat = ChatController(runtime)
    assert chat.status.mode == "ask"

    runtime.set_mode("plan")

    assert runtime.mode == "plan"
    assert runtime.gate.mode == "plan"
    assert chat.status.mode == "plan"
    assert "PLAN MODE" in runtime.agent.system_prompt


async def test_runtime_set_mode_rejects_unknown(runtime_factory):
    runtime = await runtime_factory()
    with pytest.raises(ValueError):
        runtime.set_mode("bogus")
    assert runtime.mode == "ask"


async def test_switching_plan_to_zen_lifts_the_plan_refusal():
    gate = PermissionGate(mode="plan", interactive=False)
    blocked, _ = await gate.check("write_file", {"path": "x"})
    assert blocked is False

    gate.set_mode("zen")
    allowed, reason = await gate.check("write_file", {"path": "x"})
    assert allowed is True and reason == ""
