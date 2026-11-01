"""Run controls: follow-up queue, interrupt and queue clearing."""

import asyncio

from zenagent.cli.tui.app import ZenAgentApp
from zenagent.cli.tui.widgets.error_banner import ErrorBanner
from zenagent.cli.tui.widgets.pending_band import PendingBand
from zenagent.cli.tui.widgets.prompt_input import PromptInput
from zenagent.cli.tui.widgets.status_band import StatusBand
from zenagent.events import AssistantEvent
from zenagent.types import AssistantMessage, TextContent, Usage


async def test_queue_is_visible_and_ctrl_c_aborts(gated_app):
    app, gate = gated_app
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await app.chat.submit("hi")
        await pilot.pause()
        assert app.chat.status.running is True

        app.chat.queue_follow_up("queued message")
        await pilot.pause()
        assert "1 queued" in app.query_one(StatusBand).plain

        await pilot.press("ctrl+c")
        gate.set()
        await pilot.pause()
        await app.chat.wait_for_idle()
        await pilot.pause()
        assert app.query_one(StatusBand) is not None  # app survived
        assert app.chat.status.running is False


async def test_alt_up_puts_the_last_queued_message_back_in_the_prompt(gated_app):
    app, gate = gated_app
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await app.chat.submit("first")
        await pilot.pause()

        prompt = app.query_one(PromptInput)
        prompt.insert("queued text")
        await pilot.press("ctrl+q")
        await pilot.pause()
        assert app.chat.status.follow_ups == 1

        await pilot.press("alt+up")
        await pilot.pause()
        assert prompt.text == "queued text"
        assert app.chat.status.follow_ups == 0

        gate.set()
        await app.chat.wait_for_idle()


async def test_double_escape_with_an_empty_prompt_undoes_the_last_turn(fake_runtime):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await app.chat.submit("hi")
        await app.chat.wait_for_idle()
        await pilot.pause()
        assert fake_runtime.agent.messages

        await pilot.press("escape")
        await pilot.press("escape")
        await pilot.pause()
        await pilot.pause()
        assert fake_runtime.agent.messages == []


async def test_single_escape_does_not_undo(fake_runtime):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await app.chat.submit("hi")
        await app.chat.wait_for_idle()
        await pilot.pause()

        await pilot.press("escape")
        await pilot.pause()
        assert fake_runtime.agent.messages


async def test_escape_while_running_only_aborts(gated_app):
    app, gate = gated_app
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await app.chat.submit("first")
        await pilot.pause()
        await pilot.press("escape")
        gate.set()
        await pilot.pause()
        await app.chat.wait_for_idle()
        # abort must not be mistaken for the double-escape rewind
        assert app.runtime.agent.messages, "a running Esc must never undo the turn"


async def test_mid_run_submit_shows_the_pending_band(gated_app):
    app, gate = gated_app
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await app.chat.submit("hi")
        await pilot.pause()

        prompt = app.query_one(PromptInput)
        prompt.focus()
        prompt.insert("later")
        await pilot.press("enter")
        await pilot.pause()

        band = app.query_one(PendingBand)
        assert band.display is True
        assert "Queued · 1" in band.plain
        assert "later" in band.plain

        gate.set()
        await app.chat.wait_for_idle()
        await pilot.pause()
        assert app.query_one(PendingBand).display is False


async def test_ctrl_s_with_an_empty_prompt_promotes_the_newest_queued(gated_app):
    app, gate = gated_app
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await app.chat.submit("hi")
        await pilot.pause()

        app.chat.queue_follow_up("one")
        app.chat.queue_follow_up("two")
        app.query_one(PromptInput).focus()
        await pilot.press("ctrl+s")
        await pilot.pause()

        assert [(p.lane, p.text) for p in app.chat.status.pending] == [
            ("queue", "one"),
            ("steer", "two"),
        ]
        assert "Steering · 1" in app.query_one(PendingBand).plain

        gate.set()
        await app.chat.wait_for_idle()


async def test_ctrl_x_clears_both_lanes(gated_app):
    app, gate = gated_app
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await app.chat.submit("hi")
        await pilot.pause()

        app.chat.queue_follow_up("one")
        app.chat.steer("two")
        await pilot.pause()
        assert app.query_one(PendingBand).display is True

        await pilot.press("ctrl+x")
        await pilot.pause()

        assert app.chat.status.pending == []
        assert app.chat.runtime.agent.pending_steering == 0
        assert app.query_one(PendingBand).display is False

        gate.set()
        await app.chat.wait_for_idle()


async def test_pending_band_follows_the_dock_width_with_the_sidebar_open(gated_app):
    app, gate = gated_app
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await app.chat.submit("hi")
        await pilot.pause()

        app.chat.queue_follow_up("a long queued message " * 6)
        await pilot.press("ctrl+b")   # 打开侧栏，dock 变窄
        await pilot.pause()

        band = app.query_one(PendingBand)
        assert band.display is True
        assert max(len(line) for line in band.plain.splitlines()) <= app._band_width()

        await pilot.press("ctrl+b")
        gate.set()
        await app.chat.wait_for_idle()


async def test_error_keeps_the_banner_and_the_demoted_steer_visible(runtime_factory):
    gate = asyncio.Event()
    calls: list[int] = []

    async def stream(model, messages, tools, options):
        calls.append(1)
        partial = AssistantMessage(
            content=[TextContent(text="")], provider="p", model="m",
            usage=Usage(), stop_reason="pending", timestamp=0,
        )
        yield AssistantEvent(type="start", partial=partial)
        await gate.wait()
        partial.stop_reason = "error"
        yield AssistantEvent(type="error", partial=partial, reason="error")

    runtime = await runtime_factory(stream_fn=stream)
    app = ZenAgentApp(runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await app.chat.submit("hi")
        await pilot.pause()
        app.chat.steer("save the logs")
        gate.set()
        await app.chat.wait_for_idle()
        await pilot.pause()
        await pilot.pause()

        assert len(calls) == 1, "an error ending must not auto-run the queue"
        banner = app.query_one(ErrorBanner)
        assert banner.display is True and banner.plain, "the error stays readable"
        band = app.query_one(PendingBand)
        assert band.display is True
        assert "Queued · 1" in band.plain
        assert "save the logs" in band.plain


async def test_alt_shift_p_toggles_plan_mode_and_back(picker_app):
    app = picker_app
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert app.runtime.mode != "plan"
        await pilot.press("alt+shift+p")
        await pilot.pause()
        assert app.runtime.mode == "plan"
        await pilot.press("alt+shift+p")
        await pilot.pause()
        assert app.runtime.mode == "ask"


async def test_shift_ctrl_p_cycles_models_backward(picker_app, monkeypatch):
    from dataclasses import replace
    from types import SimpleNamespace

    app = picker_app
    app.runtime.agent.model = replace(app.runtime.agent.model, id="b")
    entries = [SimpleNamespace(id=name, available=True) for name in ("a", "b", "c")]
    monkeypatch.setattr(app.models, "entries", lambda: entries)
    applied: list[str] = []
    monkeypatch.setattr(app, "_apply_model", applied.append)

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("shift+ctrl+p")
        await pilot.pause()
        assert applied == ["a"], "backward cycle must pick the previous model"

        await pilot.press("ctrl+p")
        await pilot.pause()
        assert applied == ["a", "c"], "forward cycle keeps working"


async def test_f5_and_alt_r_dispatch_the_shared_retry_command(picker_app, monkeypatch):
    app = picker_app
    calls: list[tuple[str, str]] = []

    async def capture(name: str, argument: str = "") -> None:
        calls.append((name, argument))

    monkeypatch.setattr(app, "_dispatch_command", capture)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("f5")
        await pilot.pause()
        await pilot.press("alt+r")
        await pilot.pause()
    assert calls == [("retry", ""), ("retry", "")]


async def test_ctrl_g_replaces_the_draft_with_the_edited_text(picker_app, monkeypatch):
    app = picker_app
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        prompt = app.query_one(PromptInput)
        prompt.insert("draft")
        monkeypatch.setattr(
            app, "edit_draft_externally", lambda text: text + " (edited)"
        )
        await pilot.press("ctrl+g")
        await pilot.pause()
        assert prompt.text == "draft (edited)"


async def test_ctrl_g_keeps_the_draft_when_editing_is_cancelled(
    picker_app, monkeypatch
):
    app = picker_app
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        prompt = app.query_one(PromptInput)
        prompt.insert("draft")
        monkeypatch.setattr(app, "edit_draft_externally", lambda text: None)
        await pilot.press("ctrl+g")
        await pilot.pause()
        assert prompt.text == "draft"


async def test_external_editor_without_a_configured_editor_notifies(
    picker_app, monkeypatch
):
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.delenv("EDITOR", raising=False)
    app = picker_app
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert app.edit_draft_externally("draft") is None


async def test_ctrl_v_attaches_an_image_path_from_the_clipboard(
    picker_app, monkeypatch, tmp_path
):
    from zenagent.cli.tui import clipboard

    image = tmp_path / "shot.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 32)
    monkeypatch.setattr(clipboard, "read_clipboard_text", lambda: str(image))
    app = picker_app
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+v")
        await pilot.pause()
        assert len(app.chat.attachments) == 1
        assert app.chat.attachments[0].mime_type == "image/png"


async def test_ctrl_v_pastes_plain_text_into_the_draft(picker_app, monkeypatch):
    from zenagent.cli.tui import clipboard

    monkeypatch.setattr(clipboard, "read_clipboard_text", lambda: "pasted words")
    app = picker_app
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        prompt = app.query_one(PromptInput)
        await pilot.press("ctrl+v")
        await pilot.pause()
        assert prompt.text == "pasted words"


async def test_plan_review_approve_switches_to_full(picker_app, monkeypatch):
    app = picker_app
    monkeypatch.setattr(app.chat, "last_assistant_text", lambda: "1. do X")
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert app.runtime.mode != "full"
        app.open_plan_review()
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
        assert app.runtime.mode == "full"


async def test_plan_review_keep_leaves_the_mode_alone(picker_app, monkeypatch):
    app = picker_app
    monkeypatch.setattr(app.chat, "last_assistant_text", lambda: "1. do X")
    before = app.runtime.mode
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.open_plan_review()
        await pilot.pause()
        await pilot.press("k")
        await pilot.pause()
        assert app.runtime.mode == before


async def test_plan_review_without_a_plan_does_not_open(picker_app, monkeypatch):
    app = picker_app
    monkeypatch.setattr(app.chat, "last_assistant_text", lambda: "")
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.open_plan_review()
        await pilot.pause()
        assert app.runtime.mode != "full"


async def test_escape_clears_the_picker_search_before_closing(picker_app):
    from zenagent.cli.tui.screens.modals import ModelPicker

    app = picker_app
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.open_model_picker()
        await pilot.pause()
        assert isinstance(app.screen, ModelPicker)
        picker = app.screen

        picker.query_one("#palette-search").value = "deep"
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, ModelPicker), "first escape only clears the search"
        assert picker.query_one("#palette-search").value == ""

        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, ModelPicker)


async def test_ctrl_shift_o_hides_and_shows_tool_cards(chat_app_with_one_tool):
    from zenagent.cli.tui.widgets.rows import ReadGroup

    app = chat_app_with_one_tool
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await app.chat.submit("go")
        await app.chat.wait_for_idle()
        await pilot.pause()
        await pilot.pause()
        group = app.query(ReadGroup).first()
        assert group.display is True

        await pilot.press("ctrl+shift+o")
        await pilot.pause()
        assert group.display is False

        await pilot.press("ctrl+shift+o")
        await pilot.pause()
        assert group.display is True


async def test_alt_l_restores_the_default_display(chat_app_with_one_tool):
    from zenagent.cli.tui.widgets.rows import ReadGroup

    app = chat_app_with_one_tool
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await app.chat.submit("go")
        await app.chat.wait_for_idle()
        await pilot.pause()
        await pilot.pause()
        await pilot.press("ctrl+shift+o")
        await pilot.pause()
        assert app.query(ReadGroup).first().display is False

        await pilot.press("alt+l")
        await pilot.pause()
        assert app.query(ReadGroup).first().display is True


async def test_reopening_a_menu_does_not_stack_a_second_panel(picker_app):
    from zenagent.cli.tui.screens.modals import ModelPicker

    app = picker_app
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.open_model_picker()
        await pilot.pause()
        screens = sum(isinstance(screen, ModelPicker) for screen in app.screen_stack)
        assert screens == 1

        app.open_model_picker()
        await pilot.pause()
        screens = sum(isinstance(screen, ModelPicker) for screen in app.screen_stack)
        assert screens == 1


async def test_hotkeys_command_opens_the_sheet(picker_app):
    from zenagent.cli.tui.screens.modals import HotkeysSheet

    app = picker_app
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await app._dispatch_command("hotkeys", "")
        await pilot.pause()
        assert isinstance(app.screen, HotkeysSheet)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, HotkeysSheet)


async def test_usage_command_opens_the_dashboard(picker_app):
    from zenagent.cli.tui.screens.modals import UsageDashboard

    app = picker_app
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await app._dispatch_command("usage", "")
        await pilot.pause()
        assert isinstance(app.screen, UsageDashboard)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, UsageDashboard)


async def test_mcp_add_wizard_saves_a_server(picker_app, monkeypatch):
    from zenagent.cli.tui.screens.modals import McpAdd

    saved: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(
        "zenagent.mcp.config.save_server",
        lambda name, command: saved.append((name, command)),
    )
    app = picker_app
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.open_mcp_add()
        await pilot.pause()
        assert isinstance(app.screen, McpAdd)
        screen = app.screen
        screen.query_one("#mcp-name").value = "demo"
        screen.query_one("#mcp-command").value = "npx -y demo"
        screen.query_one("#mcp-command").focus()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
    assert saved == [("demo", ["npx", "-y", "demo"])]


async def test_todo_editor_writes_the_session_sidecar(picker_app):
    from zenagent.cli.tui.screens.modals import TodoEditor
    from zenagent.tools.todo import todo_sidecar_path

    app = picker_app
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.open_todo_editor()
        await pilot.pause()
        assert isinstance(app.screen, TodoEditor)
        screen = app.screen
        screen.query_one("#todo-new").value = "write docs"
        screen.action_add()
        await pilot.pause()
        assert "write docs" in str(screen.query_one("#todo-list").get_option_at_index(0).prompt)

        await pilot.press("escape")
        await pilot.pause()
        await pilot.pause()

    sidecar = todo_sidecar_path(app.runtime.session.path)
    assert sidecar.is_file()
    assert "write docs" in sidecar.read_text(encoding="utf-8")
    assert any(
        task.get("content") == "write docs"
        for phase in app.chat.todos
        for task in (phase.get("tasks") or [])
    )
