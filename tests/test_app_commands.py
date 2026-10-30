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


async def test_mode_command_switches_the_runtime(services):
    from zenagent.app.commands import build_default_commands

    registry = build_default_commands(services)
    result = await registry.dispatch("mode", "plan")
    assert services.runtime.mode == "plan"
    assert result.message and "plan" in result.message


async def test_mode_command_without_argument_opens_the_selector(services):
    from zenagent.app.commands import build_default_commands

    registry = build_default_commands(services)
    result = await registry.dispatch("mode", "")
    assert result.screen == "mode-picker"


async def test_mode_command_rejects_an_unknown_mode(services):
    from zenagent.app.commands import build_default_commands

    registry = build_default_commands(services)
    result = await registry.dispatch("mode", "yolo")
    assert result.message and "unknown mode" in result.message
    assert services.runtime.mode == "ask"


async def test_rename_command_sets_the_current_session_name(services):
    from zenagent.agent.session import session_title
    from zenagent.app.commands import build_default_commands

    registry = build_default_commands(services)
    result = await registry.dispatch("rename", "fix the login bug")
    assert session_title(services.sessions.current()) == "fix the login bug"
    assert result.message and "fix the login bug" in result.message


async def test_rename_command_without_argument_reports_the_current_name(services):
    from zenagent.app.commands import build_default_commands

    registry = build_default_commands(services)
    result = await registry.dispatch("rename", "")
    assert result.message and "(empty)" in result.message


async def test_thinking_command_without_argument_opens_the_selector(services):
    from zenagent.app.commands import build_default_commands

    registry = build_default_commands(services)
    result = await registry.dispatch("thinking", "")
    assert result.screen == "thinking-picker"


async def test_tools_lists_names_and_descriptions(services):
    from zenagent.app.commands import build_default_commands

    registry = build_default_commands(services)
    result = await registry.dispatch("tools", "")
    names = [tool.name for tool in services.runtime.agent.tools]
    assert result.message and "read_file" in result.message and "bash" in result.message
    assert f"tools ({len(names)})" in result.message
