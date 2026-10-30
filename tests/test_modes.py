"""plan/ask/full runtime modes: resolution, gate behavior, prompt note."""

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


def test_modes_are_plan_ask_full():
    assert MODES == ("plan", "ask", "full")
    assert DEFAULT_MODE == "ask"


@pytest.mark.parametrize("raw", ["plan", "PLAN", " Ask ", "full"])
def test_normalize_mode_accepts_case_and_space(raw):
    assert normalize_mode(raw) in MODES


@pytest.mark.parametrize("raw", ["yolo", "", None])
def test_normalize_mode_rejects_unknown(raw):
    assert normalize_mode(raw) is None


def test_explicit_mode_wins_over_flags():
    assert resolve_mode(explicit="full", assume_yes=False, read_only=True) == "full"


def test_yes_maps_to_full():
    assert resolve_mode(explicit=None, assume_yes=True) == "full"


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


async def test_full_allows_write_without_yes():
    gate = PermissionGate(mode="full", interactive=False)
    allowed, reason = await gate.check("write_file", {"path": "x"})
    assert allowed is True and reason == ""


async def test_ask_still_denies_in_non_interactive_runs():
    gate = PermissionGate(mode="ask", interactive=False)
    allowed, reason = await gate.check("write_file", {"path": "x"})
    assert allowed is False and "non-interactive" in reason


async def test_full_still_refuses_catastrophic_commands():
    gate = PermissionGate(mode="full", allow_dangerous=False)
    allowed, reason = await gate.check("bash", {"command": "rm -rf /"})
    assert allowed is False and "--allow-dangerous" in reason


async def test_deny_pattern_still_beats_full():
    gate = PermissionGate(mode="full", deny=["write_file"])
    allowed, reason = await gate.check("write_file", {"path": "x"})
    assert allowed is False and "denied by policy" in reason


def test_set_mode_switches_gate():
    gate = PermissionGate(mode="ask", interactive=False)
    gate.set_mode("full")
    assert gate.mode == "full"


def test_set_mode_rejects_unknown():
    with pytest.raises(ValueError):
        PermissionGate().set_mode("bogus")


def test_plan_prompt_note_is_explicit_about_read_only():
    note = mode_prompt_note("plan")
    assert "PLAN" in note
    assert "read-only" in note.lower()


@pytest.mark.parametrize("mode", ["ask", "full"])
def test_non_plan_prompt_note_is_empty(mode):
    assert mode_prompt_note(mode) == ""


def test_describe_modes_lists_all_three():
    text = describe_modes()
    for mode in MODES:
        assert mode in text


async def test_runtime_starts_in_ask_and_exposes_mode(runtime_factory):
    runtime = await runtime_factory()
    assert runtime.mode == "ask"


async def test_runtime_options_can_select_mode(runtime_factory):
    runtime = await runtime_factory(mode="full")
    assert runtime.mode == "full"


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


async def test_switching_plan_to_full_lifts_the_plan_refusal():
    gate = PermissionGate(mode="plan", interactive=False)
    blocked, _ = await gate.check("write_file", {"path": "x"})
    assert blocked is False

    gate.set_mode("full")
    allowed, reason = await gate.check("write_file", {"path": "x"})
    assert allowed is True and reason == ""
