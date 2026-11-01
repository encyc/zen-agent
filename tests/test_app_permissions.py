"""PermissionBroker: deny-by-default, read tools pass, session grants stick."""

from zenagent.app.permissions import (
    PermissionBroker,
    PermissionGate,
    build_permission_gate,
)
from zenagent.app.safety import GuardPolicy
from zenagent.types import ToolCall


async def test_deny_all_blocks_writes_but_allows_reads():
    broker = PermissionBroker.deny_all()

    blocked = await broker.before_tool_call(
        ToolCall(id="1", name="write_file", arguments={"path": "x"}), {"path": "x"}
    )
    assert blocked and blocked["block"] is True

    allowed = await broker.before_tool_call(
        ToolCall(id="2", name="read_file", arguments={"path": "x"}), {"path": "x"}
    )
    assert allowed is None


async def test_allow_for_session_persists_within_session():
    broker = PermissionBroker.deny_all()
    broker.allow_for_session("write_file")
    assert (
        await broker.before_tool_call(
            ToolCall(id="1", name="write_file", arguments={}), {}
        )
        is None
    )


async def test_broker_ask_mode_confirms_mutating_tools():
    gate = PermissionGate(interactive=False, assume_yes=True, mode="ask")
    broker = PermissionBroker.wrap(gate)
    answers = ["deny", "allow"]
    calls: list[str] = []

    async def confirm(name, arguments, verdict):
        calls.append(name)
        return answers.pop(0)

    broker.set_confirm(confirm)
    call = ToolCall(id="1", name="write_file", arguments={"path": "x"})

    denied = await broker.before_tool_call(call, {"path": "x"})
    assert denied is not None and denied["block"] is True
    allowed = await broker.before_tool_call(call, {"path": "x"})
    assert allowed is None
    assert calls == ["write_file", "write_file"]


async def test_broker_always_remembers_the_tool_for_the_session():
    gate = PermissionGate(interactive=False, assume_yes=True, mode="ask")
    broker = PermissionBroker.wrap(gate)

    async def confirm(name, arguments, verdict):
        return "always"

    broker.set_confirm(confirm)
    call = ToolCall(id="1", name="write_file", arguments={"path": "x"})
    assert await broker.before_tool_call(call, {"path": "x"}) is None

    asked: list[str] = []

    async def deny(name, arguments, verdict):
        asked.append(name)
        return "deny"

    broker.set_confirm(deny)
    assert await broker.before_tool_call(call, {"path": "x"}) is None
    assert asked == []


async def test_broker_full_mode_skips_confirmation():
    gate = PermissionGate(interactive=False, assume_yes=True, mode="full")
    broker = PermissionBroker.wrap(gate)
    asked: list[str] = []

    async def confirm(name, arguments, verdict):
        asked.append(name)
        return "deny"

    broker.set_confirm(confirm)
    call = ToolCall(id="1", name="write_file", arguments={"path": "x"})
    assert await broker.before_tool_call(call, {"path": "x"}) is None
    assert asked == []


async def test_broker_plan_mode_blocks_before_asking():
    gate = PermissionGate(interactive=False, assume_yes=True, mode="plan")
    broker = PermissionBroker.wrap(gate)
    asked: list[str] = []

    async def confirm(name, arguments, verdict):
        asked.append(name)
        return "allow"

    broker.set_confirm(confirm)
    call = ToolCall(id="1", name="write_file", arguments={"path": "x"})
    blocked = await broker.before_tool_call(call, {"path": "x"})
    assert blocked is not None and blocked["block"] is True
    assert "plan" in blocked["reason"].lower()
    assert asked == []


async def test_broker_exposes_mode_and_switches_it():
    broker = PermissionBroker.wrap(PermissionGate(interactive=False, mode="ask"))
    assert broker.mode == "ask"
    broker.set_mode("full")
    assert broker.mode == "full" and broker._gate.mode == "full"


async def test_broker_plan_mode_beats_a_session_grant():
    gate = PermissionGate(interactive=False, assume_yes=True, mode="plan")
    broker = PermissionBroker.wrap(gate)
    broker.allow_for_session("write_file")
    call = ToolCall(id="1", name="write_file", arguments={"path": "x"})
    blocked = await broker.before_tool_call(call, {"path": "x"})
    assert blocked is not None and blocked["block"] is True
    assert "plan" in blocked["reason"].lower()


async def test_broker_deny_pattern_beats_a_session_grant():
    gate = PermissionGate(
        interactive=False, assume_yes=True, mode="ask", deny=["write_file"]
    )
    broker = PermissionBroker.wrap(gate)
    broker.allow_for_session("write_file")
    call = ToolCall(id="1", name="write_file", arguments={"path": "x"})
    blocked = await broker.before_tool_call(call, {"path": "x"})
    assert blocked is not None and blocked["block"] is True
    assert "denied by policy" in blocked["reason"]


# ---------------------------------------------------------------------------
# Risk-level confirmations: full mode still asks; grants are target-scoped
# ---------------------------------------------------------------------------


async def test_full_mode_still_asks_for_outside_write(tmp_path):
    asked: list[tuple[str, str | None]] = []

    async def confirm(name, arguments, verdict):
        asked.append((name, verdict.risk))
        return "allow"

    gate = build_permission_gate(
        interactive=False, mode="full", policy=GuardPolicy(workspace=tmp_path)
    )
    broker = PermissionBroker.wrap(gate)
    broker.set_confirm(confirm)
    args = {"path": "/etc/hosts", "content": "x"}
    assert await broker.before_tool_call("write_file", args) is None
    assert asked == [("write_file", "outside")]


async def test_ordinary_write_in_full_mode_does_not_ask(tmp_path):
    asked: list[str] = []

    async def confirm(name, arguments, verdict):
        asked.append(name)
        return "deny"

    gate = build_permission_gate(
        interactive=False, mode="full", policy=GuardPolicy(workspace=tmp_path)
    )
    broker = PermissionBroker.wrap(gate)
    broker.set_confirm(confirm)
    assert (
        await broker.before_tool_call(
            "write_file", {"path": str(tmp_path / "a.py"), "content": "x"}
        )
        is None
    )
    assert asked == []


async def test_always_on_risk_is_target_scoped(tmp_path):
    answers = ["always", "deny"]
    asked: list[str] = []

    async def confirm(name, arguments, verdict):
        asked.append(str(arguments.get("path")))
        return answers.pop(0) if answers else "deny"

    gate = build_permission_gate(
        interactive=False, mode="full", policy=GuardPolicy(workspace=tmp_path)
    )
    broker = PermissionBroker.wrap(gate)
    broker.set_confirm(confirm)
    first = {"path": "/etc/hosts", "content": "x"}
    assert await broker.before_tool_call("write_file", first) is None
    assert await broker.before_tool_call("write_file", first) is None
    assert len(asked) == 1
    assert (
        await broker.before_tool_call(
            "write_file", {"path": "/etc/passwd", "content": "x"}
        )
        is not None
    )


async def test_destructive_bash_in_full_mode_asks(tmp_path):
    seen: list[str | None] = []

    async def confirm(name, arguments, verdict):
        seen.append(verdict.risk)
        return "deny"

    gate = build_permission_gate(
        interactive=False, mode="full", policy=GuardPolicy(workspace=tmp_path)
    )
    broker = PermissionBroker.wrap(gate)
    broker.set_confirm(confirm)
    blocked = await broker.before_tool_call("bash", {"command": "git reset --hard"})
    assert blocked is not None and blocked["block"] is True
    assert seen == ["destructive"]


async def test_plan_mode_still_refuses_risky_write(tmp_path):
    asked: list[str] = []

    async def confirm(name, arguments, verdict):
        asked.append(name)
        return "allow"

    gate = build_permission_gate(
        interactive=False, mode="plan", policy=GuardPolicy(workspace=tmp_path)
    )
    broker = PermissionBroker.wrap(gate)
    broker.set_confirm(confirm)
    blocked = await broker.before_tool_call(
        "write_file", {"path": "/etc/hosts", "content": "x"}
    )
    assert blocked is not None and blocked["block"] is True
    assert "plan mode" in blocked["reason"]
    assert asked == []
