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


async def test_dirs_track_extra_roots(services, tmp_path):
    extra = tmp_path / "other"
    extra.mkdir()
    registry = build_default_commands(services)

    assert str(extra) not in (await registry.dispatch("dirs", "")).message
    added = await registry.dispatch("add-dir", str(extra))
    assert str(extra) in added.message
    assert services.runtime.extra_dirs == [extra]
    listed = await registry.dispatch("dirs", "")
    assert str(extra) in listed.message
    removed = await registry.dispatch("remove-dir", str(extra))
    assert str(extra) in removed.message
    assert services.runtime.extra_dirs == []


async def test_add_dir_rejects_a_missing_path(services, tmp_path):
    registry = build_default_commands(services)
    result = await registry.dispatch("add-dir", str(tmp_path / "nope"))
    assert "not a tracked directory" in (result.message or "")


async def test_copy_uses_the_clipboard(services, monkeypatch):
    from zenagent.cli.tui import clipboard

    copied: list[str] = []
    monkeypatch.setattr(clipboard, "write_clipboard_text", lambda text: copied.append(text) or True)
    registry = build_default_commands(services)
    result = await registry.dispatch("copy", "hello")
    assert copied == ["hello"]
    assert "copied" in (result.message or "")


async def test_copy_reports_a_missing_clipboard(services, monkeypatch):
    from zenagent.cli.tui import clipboard

    monkeypatch.setattr(clipboard, "write_clipboard_text", lambda text: False)
    registry = build_default_commands(services)
    result = await registry.dispatch("copy", "hello")
    assert "clipboard tool" in (result.message or "")


async def test_shake_rejects_an_unknown_kind(services):
    registry = build_default_commands(services)
    result = await registry.dispatch("shake", "everything")
    assert "usage" in (result.message or "")


async def test_jobs_lists_nothing_by_default(services):
    registry = build_default_commands(services)
    result = await registry.dispatch("jobs", "")
    assert result.message in ("no background jobs", "没有后台作业")


async def test_fresh_returns_the_display_reset_screen(services):
    registry = build_default_commands(services)
    result = await registry.dispatch("fresh", "")
    assert result.screen == "display-reset"


async def test_trace_is_off_without_a_trace_file(services):
    registry = build_default_commands(services)
    result = await registry.dispatch("trace", "")
    assert "not enabled" in (result.message or "")
    services.runtime.trace_path = "/tmp/trace.jsonl"
    result = await registry.dispatch("trace", "")
    assert "/tmp/trace.jsonl" in (result.message or "")


async def test_btw_requires_a_question(services):
    registry = build_default_commands(services)
    result = await registry.dispatch("btw", "")
    assert "usage" in (result.message or "")


async def test_hotkeys_and_usage_return_their_overlays(services):
    registry = build_default_commands(services)
    hotkeys = await registry.dispatch("hotkeys", "")
    assert hotkeys.screen == "hotkeys"
    assert hotkeys.message
    usage = await registry.dispatch("usage", "")
    assert usage.screen == "usage-dashboard"


async def test_mcp_add_returns_the_wizard_screen(services):
    registry = build_default_commands(services)
    result = await registry.dispatch("mcp", "add")
    assert result.screen == "mcp-add"
    assert "TUI" in (result.message or "")


async def test_todo_command_opens_the_editor(services):
    registry = build_default_commands(services)
    result = await registry.dispatch("todo", "")
    assert result.screen == "todo-edit"


async def services_with_answer(runtime_factory, answer):
    """AppServices whose chat has already answered once with `answer`."""
    from conftest import sequenced_stream

    from zenagent.app.chat import ChatController
    from zenagent.app.commands import AppServices
    from zenagent.app.models import ModelManager
    from zenagent.app.sessions import SessionManager

    runtime = await runtime_factory(stream_fn=sequenced_stream([answer]))
    chat = ChatController(runtime)
    await chat.submit("hi")
    await chat.wait_for_idle()
    return AppServices(runtime=runtime, chat=chat,
                       models=ModelManager(runtime), sessions=SessionManager(runtime))


async def test_urls_command_numbers_links_from_the_last_answer(runtime_factory):
    answer = (
        "先起回调：\n\n```\nhttps://metabase.example.cn/oauth/authorize?"
        "response_type=code&redirect_uri=http%3A%2F%2F127.0.0.1%3A8971%2Fcallback\n```\n"
        "文档见 [docs](https://example.com/docs)。\n"
    )
    services = await services_with_answer(runtime_factory, answer)
    result = await build_default_commands(services).dispatch("urls", "")
    assert result.message is not None
    assert "1. https://metabase.example.cn/oauth/authorize" in result.message
    assert "2. https://example.com/docs" in result.message


async def test_urls_command_reports_when_the_answer_has_no_links(services):
    result = await build_default_commands(services).dispatch("urls", "")
    assert result.message == "no links in the last answer"


async def test_copy_with_an_index_copies_the_url_verbatim(runtime_factory, monkeypatch):
    copied: list[str] = []
    monkeypatch.setattr(
        "zenagent.cli.tui.clipboard.write_clipboard_text",
        lambda text: copied.append(text) or True,
    )
    answer = (
        "```\nhttps://metabase.example.cn/oauth/authorize?response_type=code"
        "&redirect_uri=http%3A%2F%2F127.0.0.1%3A8971%2Fcallback\n```\n"
    )
    services = await services_with_answer(runtime_factory, answer)
    result = await build_default_commands(services).dispatch("copy", "1")
    assert copied and "%3A%2F%2F" in copied[0]
    assert result.message is not None


async def test_copy_keeps_literal_text_when_the_argument_is_not_a_number(services, monkeypatch):
    copied: list[str] = []
    monkeypatch.setattr(
        "zenagent.cli.tui.clipboard.write_clipboard_text",
        lambda text: copied.append(text) or True,
    )
    await build_default_commands(services).dispatch("copy", "hello world")
    assert copied == ["hello world"]


async def test_copy_with_an_out_of_range_index_reports_the_range(runtime_factory):
    from zenagent.i18n import t

    services = await services_with_answer(runtime_factory, "见 https://example.com/only 。")
    result = await build_default_commands(services).dispatch("copy", "3")
    assert result.message == t("cmdmsg.url_index_invalid", count=1)


async def test_copy_with_an_index_needs_a_link_in_the_last_answer(services):
    result = await build_default_commands(services).dispatch("copy", "9")
    assert result.message == "no links in the last answer"


async def test_copy_index_zero_never_copies_the_digit(services, monkeypatch):
    """`/copy 0` is an index (out of range), never the literal text "0"."""
    copied: list[str] = []
    monkeypatch.setattr(
        "zenagent.cli.tui.clipboard.write_clipboard_text",
        lambda text: copied.append(text) or True,
    )
    result = await build_default_commands(services).dispatch("copy", "0")
    assert copied == []
    assert result.message is not None


async def test_open_command_opens_the_indexed_link(runtime_factory, monkeypatch):
    import webbrowser

    opened: list[str] = []
    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **kw: opened.append(url) or True)
    services = await services_with_answer(runtime_factory, "见 https://example.com/docs 。")
    result = await build_default_commands(services).dispatch("open", "1")
    assert opened == ["https://example.com/docs"]
    assert result.message is not None


async def test_open_command_survives_a_refusing_browser(runtime_factory, monkeypatch):
    """`webbrowser.open` false or raising must not tear the app down."""
    import webbrowser

    def boom(url, *args, **kwargs):
        raise OSError("no browser")

    monkeypatch.setattr(webbrowser, "open", boom)
    from zenagent.i18n import t

    services = await services_with_answer(runtime_factory, "见 https://example.com/x 。")
    result = await build_default_commands(services).dispatch("open", "1")
    assert result.message == t("cmdmsg.url_open_failed", url="https://example.com/x")


async def test_open_command_without_an_index_prints_usage(services):
    result = await build_default_commands(services).dispatch("open", "")
    assert result.message is not None and "/urls" in result.message


async def memory_services(runtime_factory, tmp_path):
    """AppServices whose memory store points at an empty tmp library."""
    from zenagent.agent.memory_store import MemoryStore
    from zenagent.app.chat import ChatController
    from zenagent.app.commands import AppServices
    from zenagent.app.models import ModelManager
    from zenagent.app.sessions import SessionManager

    runtime = await runtime_factory()
    runtime.memory_store = MemoryStore(tmp_path)
    return AppServices(runtime=runtime, chat=ChatController(runtime),
                       models=ModelManager(runtime), sessions=SessionManager(runtime))


async def test_memory_bare_returns_manager_screen(runtime_factory, tmp_path):
    services = await memory_services(runtime_factory, tmp_path)
    result = await build_default_commands(services).dispatch("memory", "")
    assert result.screen == "memory-manager"


async def test_memory_add_keeps_spaced_title_prefill(runtime_factory, tmp_path):
    services = await memory_services(runtime_factory, tmp_path)
    result = await build_default_commands(services).dispatch("memory", "add 选型 原则")
    assert result.screen == "memory-add"
    assert result.target == "选型 原则"


async def test_memory_edit_missing_entry_is_a_message_not_a_screen(runtime_factory, tmp_path):
    services = await memory_services(runtime_factory, tmp_path)
    result = await build_default_commands(services).dispatch("memory", "edit beef")
    assert result.screen is None
    assert "beef" in result.message


async def test_memory_edit_existing_returns_screen_with_id(runtime_factory, tmp_path):
    services = await memory_services(runtime_factory, tmp_path)
    entry = services.runtime.memory_store.write_entry(title="T", body="B", source="test")
    result = await build_default_commands(services).dispatch("memory", f"edit {entry.id}")
    assert result.screen == "memory-edit"
    assert result.target == entry.id


async def test_memory_rm_deletes_and_reports_journal(runtime_factory, tmp_path):
    services = await memory_services(runtime_factory, tmp_path)
    entry = services.runtime.memory_store.write_entry(title="T", body="B", source="test")
    result = await build_default_commands(services).dispatch("memory", f"rm {entry.id}")
    assert "deleted" in result.message
    assert services.runtime.memory_store.read_entry(entry.id) is None


async def test_memory_id_shows_full_entry(runtime_factory, tmp_path):
    services = await memory_services(runtime_factory, tmp_path)
    entry = services.runtime.memory_store.write_entry(title="标题", body="正文", source="test")
    result = await build_default_commands(services).dispatch("memory", entry.id)
    assert "标题" in result.message and "正文" in result.message


async def test_memory_unknown_argument_shows_usage(runtime_factory, tmp_path):
    services = await memory_services(runtime_factory, tmp_path)
    result = await build_default_commands(services).dispatch("memory", "nonsense")
    assert "usage" in result.message


async def test_shake_tools_does_not_carry_tool_end_records(services):
    from zenagent.agent.session import Session
    from zenagent.types import (
        AssistantMessage,
        TextContent,
        ToolCall,
        ToolResultMessage,
        Usage,
        UserMessage,
    )

    runtime = services.runtime
    messages = [
        UserMessage(content="go", timestamp=1),
        AssistantMessage(
            content=[ToolCall(id="c1", name="bash", arguments={})],
            provider="p",
            model="m",
            usage=Usage(),
            stop_reason="tool_use",
            timestamp=2,
        ),
        ToolResultMessage(
            tool_call_id="c1",
            tool_name="bash",
            content=[TextContent(text="lots of output")],
            is_error=False,
            timestamp=3,
        ),
    ]
    runtime.agent.replace_messages(messages)
    for message in messages:
        runtime.session.append(message)
    runtime.session.append_meta(
        {"type": "tool_end", "call_id": "c1", "details": {"diff": "+x"}}
    )

    await build_default_commands(services).dispatch("shake", "tools")

    reloaded = Session(runtime.session.path)
    reloaded.load()
    assert not [item for item in reloaded.records if item["type"] == "tool_end"]
