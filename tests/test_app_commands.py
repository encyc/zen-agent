"""Command registry: dispatch to UI intents and unknown-command handling."""

from zenagent.app.commands import build_default_commands


async def test_dispatch_model_command_returns_screen(services):
    registry = build_default_commands(services)
    result = await registry.dispatch("model", "")
    assert result.screen == "model-picker"


async def test_unknown_command_returns_a_message(services):
    registry = build_default_commands(services)
    result = await registry.dispatch("nope", "")
    assert result.message is not None and "unknown command" in result.message
