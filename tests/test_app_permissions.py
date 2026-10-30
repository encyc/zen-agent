"""PermissionBroker: deny-by-default, read tools pass, session grants stick."""

from zenagent.app.permissions import PermissionBroker
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
    from zenagent.app.permissions import PermissionGate

    gate = PermissionGate(interactive=False, assume_yes=True, mode="ask")
    broker = PermissionBroker.wrap(gate)
    answers = ["deny", "allow"]
    calls: list[str] = []

    async def confirm(name, arguments):
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
    from zenagent.app.permissions import PermissionGate

    gate = PermissionGate(interactive=False, assume_yes=True, mode="ask")
    broker = PermissionBroker.wrap(gate)

    async def confirm(name, arguments):
        return "always"

    broker.set_confirm(confirm)
    call = ToolCall(id="1", name="write_file", arguments={"path": "x"})
    assert await broker.before_tool_call(call, {"path": "x"}) is None

    asked: list[str] = []

    async def deny(name, arguments):
        asked.append(name)
        return "deny"

    broker.set_confirm(deny)
    assert await broker.before_tool_call(call, {"path": "x"}) is None
    assert asked == []


async def test_broker_full_mode_skips_confirmation():
    from zenagent.app.permissions import PermissionGate

    gate = PermissionGate(interactive=False, assume_yes=True, mode="full")
    broker = PermissionBroker.wrap(gate)
    asked: list[str] = []

    async def confirm(name, arguments):
        asked.append(name)
        return "deny"

    broker.set_confirm(confirm)
    call = ToolCall(id="1", name="write_file", arguments={"path": "x"})
    assert await broker.before_tool_call(call, {"path": "x"}) is None
    assert asked == []


async def test_broker_plan_mode_blocks_before_asking():
    from zenagent.app.permissions import PermissionGate

    gate = PermissionGate(interactive=False, assume_yes=True, mode="plan")
    broker = PermissionBroker.wrap(gate)
    asked: list[str] = []

    async def confirm(name, arguments):
        asked.append(name)
        return "allow"

    broker.set_confirm(confirm)
    call = ToolCall(id="1", name="write_file", arguments={"path": "x"})
    blocked = await broker.before_tool_call(call, {"path": "x"})
    assert blocked is not None and blocked["block"] is True
    assert "plan" in blocked["reason"].lower()
    assert asked == []


async def test_broker_exposes_mode_and_switches_it():
    from zenagent.app.permissions import PermissionGate

    broker = PermissionBroker.wrap(PermissionGate(interactive=False, mode="ask"))
    assert broker.mode == "ask"
    broker.set_mode("full")
    assert broker.mode == "full" and broker._gate.mode == "full"


async def test_broker_plan_mode_beats_a_session_grant():
    from zenagent.app.permissions import PermissionGate

    gate = PermissionGate(interactive=False, assume_yes=True, mode="plan")
    broker = PermissionBroker.wrap(gate)
    broker.allow_for_session("write_file")
    call = ToolCall(id="1", name="write_file", arguments={"path": "x"})
    blocked = await broker.before_tool_call(call, {"path": "x"})
    assert blocked is not None and blocked["block"] is True
    assert "plan" in blocked["reason"].lower()


async def test_broker_deny_pattern_beats_a_session_grant():
    from zenagent.app.permissions import PermissionGate

    gate = PermissionGate(
        interactive=False, assume_yes=True, mode="ask", deny=["write_file"]
    )
    broker = PermissionBroker.wrap(gate)
    broker.allow_for_session("write_file")
    call = ToolCall(id="1", name="write_file", arguments={"path": "x"})
    blocked = await broker.before_tool_call(call, {"path": "x"})
    assert blocked is not None and blocked["block"] is True
    assert "denied by policy" in blocked["reason"]
