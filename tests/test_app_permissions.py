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
