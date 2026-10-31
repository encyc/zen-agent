"""A slash command that raises must become a notice, not an app exit.

Textual turns an exception escaping an input handler into an app shutdown
(``_handle_exception``), so one broken command used to kill the whole session --
including the ``/reload`` race where an MCP retry outlives the streaming check.
"""

from __future__ import annotations

from zenagent.cli.tui.app import ZenAgentApp
from zenagent.cli.tui.widgets.rows import NoticeRow


async def test_a_failing_command_becomes_a_notice_instead_of_an_exit(fake_runtime, monkeypatch):
    app = ZenAgentApp(fake_runtime)

    async def boom(name: str, argument: str):
        raise RuntimeError("boom")

    monkeypatch.setattr(app.commands, "dispatch", boom)

    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        await app._dispatch_command("reload", "")
        await pilot.pause()

        notices = [row.plain for row in app.query(NoticeRow)]
        assert "/reload failed: boom" in notices
        assert app.is_running


async def test_a_working_command_still_renders_its_message(fake_runtime):
    app = ZenAgentApp(fake_runtime)

    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        await app._dispatch_command("mcp", "")
        await pilot.pause()

        notices = [row.plain for row in app.query(NoticeRow)]
        assert "no MCP servers configured" in notices  # a normal message still lands
        assert app.is_running
