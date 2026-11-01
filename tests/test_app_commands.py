"""Command registry: dispatch to UI intents and unknown-command handling."""

from zenagent.app.commands import build_default_commands


async def test_dispatch_model_command_returns_screen(services):
    registry = build_default_commands(services)
    result = await registry.dispatch("model", "")
    assert result.screen == "model-picker"


async def test_model_command_with_an_argument_switches_like_mode(services):
    """`/model <id>` is the argument counterpart of `/mode <value>`.

    The handler used to drop its argument and always open the picker, so the
    value hint (``/model `` + Enter) made the user choose the same model twice.
    """
    registry = build_default_commands(services)
    result = await registry.dispatch("model", "deepseek-v4-pro")
    assert result.model == "deepseek-v4-pro"
    assert result.screen is None


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


async def test_pin_command_toggles_the_current_session(services):
    from zenagent.agent.session import read_session_pins
    from zenagent.app.commands import build_default_commands

    registry = build_default_commands(services)
    current = services.sessions.current()
    result = await registry.dispatch("pin", "")
    assert result.message and "pinned" in result.message
    assert current.name in read_session_pins(current.parent)

    result = await registry.dispatch("pin", "off")
    assert result.message and "unpinned" in result.message
    assert current.name not in read_session_pins(current.parent)


async def test_image_command_attaches_and_images_lists(services, tmp_path):
    from zenagent.app.commands import build_default_commands

    png = tmp_path / "pic.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
    registry = build_default_commands(services)

    result = await registry.dispatch("image", str(png))
    assert result.message and "attached image/png" in result.message
    assert len(services.chat.attachments) == 1

    listed = await registry.dispatch("images", "")
    assert listed.message and "image/png" in listed.message

    cleared = await registry.dispatch("images", "clear")
    assert cleared.message and services.chat.attachments == []


async def test_image_command_rejects_non_vision_models(services, monkeypatch, tmp_path):
    from zenagent.app.commands import build_default_commands

    png = tmp_path / "pic.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
    monkeypatch.setattr(services.runtime.agent.model, "supports_vision", False)

    result = await build_default_commands(services).dispatch("image", str(png))
    assert "does not accept images" in (result.message or "")
    assert services.chat.attachments == []


async def test_import_command_switches_to_the_session_file(services, tmp_path):
    from zenagent.agent.session import Session
    from zenagent.types import UserMessage

    source = tmp_path / "20260101-000000.jsonl"
    session = Session(source)
    session.append(UserMessage(content="imported prompt", timestamp=1))

    registry = build_default_commands(services)
    result = await registry.dispatch("import", str(source))

    assert result.reset_transcript is True
    assert result.message and source.name in result.message
    assert services.runtime.session.path == source
    assert [m.content for m in services.runtime.agent.messages] == ["imported prompt"]


async def test_import_command_reports_a_missing_file(services, tmp_path):
    registry = build_default_commands(services)
    result = await registry.dispatch("import", str(tmp_path / "nope.jsonl"))
    assert "no such session" in (result.message or "")


async def test_import_command_requires_a_path(services):
    registry = build_default_commands(services)
    result = await registry.dispatch("import", "")
    assert "usage" in (result.message or "")


async def test_plan_review_command_needs_a_plan(services, monkeypatch):
    registry = build_default_commands(services)
    monkeypatch.setattr(services.chat, "last_assistant_text", lambda: "")
    result = await registry.dispatch("plan-review", "")
    assert result.screen is None
    assert "no plan" in (result.message or "")


async def test_plan_review_command_opens_the_review_screen(services, monkeypatch):
    registry = build_default_commands(services)
    monkeypatch.setattr(services.chat, "last_assistant_text", lambda: "1. do X")
    result = await registry.dispatch("plan-review", "")
    assert result.screen == "plan-review"
