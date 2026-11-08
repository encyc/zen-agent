"""REPL command layer: shared registry dispatch + text rendering of UI intents.

The TUI turns ``CommandResult`` into pickers and overlays; the REPL must render
the same results as terminal text. These tests pin that mapping, the command
surface itself, and the REPL-specific integration of the shared commands.
"""

import asyncio
import time

import pytest

import zenagent.agent.session as session_module
import zenagent.cli.repl as repl_module
from zenagent.app.commands import CommandResult, build_default_commands
from zenagent.cli.repl import render_command_result, render_help, repl
from zenagent.config import UserConfig
from zenagent.events import AssistantEvent
from zenagent.providers.catalog import Model
from zenagent.providers.compat import CompatFlags
from zenagent.types import AssistantMessage, Cost, TextContent, Usage


@pytest.fixture(autouse=True)
def isolate_config(monkeypatch):
    monkeypatch.setattr("zenagent.app.bootstrap.load_user_config", lambda: UserConfig())
    monkeypatch.setattr(
        "zenagent.app.runtime.load_user_config",
        lambda *args, **kwargs: UserConfig(),
    )


@pytest.fixture(autouse=True)
def isolated_sessions(tmp_path, monkeypatch):
    root = tmp_path / "sessions"
    monkeypatch.setattr(session_module, "sessions_dir", lambda: root)
    return root


def model() -> Model:
    return Model(
        id="glm-5.3",
        provider="zai-coding-cn",
        api="openai-completions",
        base_url="https://api.test/v1",
        context_window=1000,
        max_tokens=100,
        reasoning=False,
        cost=Cost(),
        compat=CompatFlags(),
    )


def scripted_stream(*turns):
    class Stream:
        def __init__(self) -> None:
            self.turns = list(turns)
            self.requests: list[list] = []

        async def __call__(self, model_, messages, tools, options):
            self.requests.append(list(messages))
            text = self.turns.pop(0) if self.turns else "ok"
            partial = AssistantMessage(
                content=[TextContent(text=text)],
                provider="test",
                model="m",
                usage=Usage(),
                stop_reason="stop",
                timestamp=int(time.time()),
            )
            yield AssistantEvent(type="done", partial=partial, reason="stop")

    return Stream()


def scripted_input(*lines: str, settle: float = 0.02):
    pending = list(lines)

    async def read() -> str:
        await asyncio.sleep(settle)
        if not pending:
            raise EOFError
        return pending.pop(0)

    return read


def _render(services, result, lines, **kwargs):
    return render_command_result(
        result,
        name=kwargs.pop("name", "x"),
        services=services,
        print_fn=lines.append,
        keybindings=kwargs.pop("keybindings", {}),
        set_theme=kwargs.pop("set_theme", lambda spec: lines.append(f"theme:{spec}")),
        set_language=kwargs.pop(
            "set_language", lambda spec: lines.append(f"lang:{spec}")
        ),
    )


def test_registry_exposes_the_full_command_surface(services):
    registry = build_default_commands(services)
    names = {command.name for command in registry.names()}

    # The four commands the REPL used to lack are part of the shared surface.
    assert {"context", "export", "plan-review", "usage"} <= names
    assert {"model", "mode", "sessions", "compact", "retry", "restore"} <= names
    assert len(names) >= 45


def test_render_model_picker_prints_the_current_model(services):
    from zenagent.app.commands import CommandResult

    lines: list[str] = []
    _render(services, CommandResult(screen="model-picker"), lines)
    assert any(services.models.current() in line for line in lines)


def test_render_mode_and_thinking_pickers_print_current_values(services):
    from zenagent.app.commands import CommandResult

    lines: list[str] = []
    _render(services, CommandResult(screen="mode-picker"), lines)
    _render(services, CommandResult(screen="thinking-picker"), lines)
    assert any("ask" in line for line in lines)
    assert any("thinking" in line or "off" in line for line in lines)


def test_render_session_picker_lists_sessions(services):
    from zenagent.app.commands import CommandResult

    lines: list[str] = []
    _render(services, CommandResult(screen="session-picker"), lines)
    assert lines  # at least the current session row


def test_render_hotkeys_uses_the_repl_bindings(services):
    from zenagent.app.commands import CommandResult

    lines: list[str] = []
    _render(
        services,
        CommandResult(screen="hotkeys"),
        lines,
        keybindings={"accept": "enter"},
    )
    assert any("accept: enter" in line for line in lines)


def test_render_todo_and_settings_screens(services):
    from zenagent.app.commands import CommandResult

    lines: list[str] = []
    _render(services, CommandResult(screen="todo-edit"), lines)
    _render(services, CommandResult(screen="settings"), lines)
    joined = "\n".join(lines)
    assert "settings" in joined or "model" in joined


def test_render_plan_review_prints_the_plan(services, monkeypatch):
    from zenagent.app.commands import CommandResult

    monkeypatch.setattr(
        services.chat, "last_assistant_text", lambda: "1. do the thing"
    )
    lines: list[str] = []
    _render(services, CommandResult(screen="plan-review"), lines)
    assert any("do the thing" in line for line in lines)


def test_render_plan_review_without_a_plan(services):
    from zenagent.app.commands import CommandResult

    lines: list[str] = []
    _render(services, CommandResult(screen="plan-review"), lines)
    assert lines


def test_render_applies_model_appearance_and_language(services, monkeypatch):
    from zenagent.app.commands import CommandResult

    switched: list[str] = []
    monkeypatch.setattr(
        services.models,
        "switch",
        lambda model_id: switched.append(model_id)
        or type("R", (), {"ok": True, "reason": None})(),
    )
    lines: list[str] = []
    _render(
        services,
        CommandResult(model="deepseek-flash", appearance="dark", language="zh"),
        lines,
    )
    assert switched == ["deepseek-flash"]
    assert "theme:dark" in lines
    assert "lang:zh" in lines


def test_render_quit(services):
    from zenagent.app.commands import CommandResult

    assert _render(services, CommandResult(quit=True), []) == "exit"


def test_help_lists_every_registry_command(services):
    registry = build_default_commands(services)
    lines: list[str] = []
    render_help(registry, [], lines.append)
    joined = "\n".join(lines)
    for command in registry.names():
        assert f"/{command.name}" in joined, command.name


# ---------------------------------------------------------------------------
# Integration: the commands the REPL used to lack
# ---------------------------------------------------------------------------


async def test_repl_sessions_usage_context_and_hotkeys_render(isolated_sessions):
    lines: list[str] = []
    code = await repl(
        "glm-5.3",
        stream_fn=scripted_stream(),
        api_key="k",
        input_fn=scripted_input("/sessions", "/usage", "/context", "/hotkeys", "/exit"),
        print_fn=lines.append,
    )
    assert code == 0
    joined = "\n".join(lines)
    assert "msgs" in joined  # the session picker rows
    assert "context" in joined.lower()
    assert "abort:" in joined  # the REPL key table


async def test_repl_export_writes_a_markdown_file(isolated_sessions):
    lines: list[str] = []
    code = await repl(
        "glm-5.3",
        stream_fn=scripted_stream("hello"),
        api_key="k",
        input_fn=scripted_input("hi", "/export", "/exit"),
        print_fn=lines.append,
    )
    assert code == 0
    assert any("exported" in line.lower() or ".md" in line for line in lines)


async def test_repl_todo_and_settings_print(isolated_sessions):
    lines: list[str] = []
    code = await repl(
        "glm-5.3",
        stream_fn=scripted_stream(),
        api_key="k",
        input_fn=scripted_input("/todo", "/settings", "/exit"),
        print_fn=lines.append,
    )
    assert code == 0
    joined = "\n".join(lines)
    assert "settings" in joined.lower()


async def test_repl_plan_review_prints_the_plan(isolated_sessions):
    lines: list[str] = []
    code = await repl(
        "glm-5.3",
        stream_fn=scripted_stream("1. inspect the repo"),
        api_key="k",
        input_fn=scripted_input("make a plan", "/plan-review", "/exit"),
        print_fn=lines.append,
    )
    assert code == 0
    joined = "\n".join(lines)
    assert "inspect the repo" in joined


async def test_repl_compact_continues_in_the_same_session(isolated_sessions):
    from zenagent.agent.session import Session
    from zenagent.app import records

    lines: list[str] = []
    code = await repl(
        "glm-5.3",
        stream_fn=scripted_stream("first", "summary", "after"),
        api_key="k",
        input_fn=scripted_input("hi", "/compact", "next", "/exit"),
        print_fn=lines.append,
    )
    assert code == 0
    files = list(isolated_sessions.glob("*.jsonl"))
    assert len(files) == 1, "compaction must not spawn a summary session file"
    path = files[0]
    # Replay returns the compacted segment; the file keeps the full history.
    texts = [
        message.content
        for message in records.open_session(path).load()
        if isinstance(message.content, str)
    ]
    assert len(texts) == 2
    assert "此前会话摘要" in texts[0] and "summary" in texts[0]
    assert texts[1] == "next"
    full_texts = [
        message.content
        for message in Session(path).load()
        if isinstance(message.content, str)
    ]
    assert "hi" in full_texts and "next" in full_texts


async def test_repl_retry_reruns_without_duplicating_the_prompt(isolated_sessions):
    stream = scripted_stream("first", "second")
    lines: list[str] = []
    code = await repl(
        "glm-5.3",
        stream_fn=stream,
        api_key="k",
        input_fn=scripted_input("hello", "/retry", "/exit"),
        print_fn=lines.append,
    )
    assert code == 0
    assert len(stream.requests) == 2
    for request in stream.requests:
        users = [
            message
            for message in request
            if type(message).__name__ == "UserMessage"
        ]
        assert len(users) == 1 and users[0].content == "hello"


def test_every_command_screen_has_a_text_rendering(services):
    """No TUI screen may silently do nothing in the REPL."""
    import inspect
    import re

    from zenagent.app import commands as commands_module
    from zenagent.app.commands import CommandResult

    screens = set(re.findall(r'screen="([a-z-]+)"', inspect.getsource(commands_module)))
    assert screens
    for screen in sorted(screens):
        lines: list[str] = []
        assert _render(services, CommandResult(screen=screen), lines) is None
        if screen in {
            "model-picker",
            "mode-picker",
            "thinking-picker",
            "theme-picker",
            "language-picker",
            "session-picker",
            "session-picker-all",
            "hotkeys",
            "todo-edit",
            "settings",
            "plan-review",
        }:
            assert lines, screen


async def test_repl_trace_flag_reports_the_path(isolated_sessions, tmp_path):
    trace = tmp_path / "trace.jsonl"
    lines: list[str] = []
    code = await repl(
        "glm-5.3",
        stream_fn=scripted_stream(),
        api_key="k",
        trace=trace,
        input_fn=scripted_input("/trace", "/exit"),
        print_fn=lines.append,
    )
    assert code == 0
    assert str(trace) in "\n".join(lines)


async def test_repl_undo_continues_in_a_new_session(isolated_sessions):
    from zenagent.agent.session import Session

    lines: list[str] = []
    code = await repl(
        "glm-5.3",
        stream_fn=scripted_stream("first", "second"),
        api_key="k",
        input_fn=scripted_input("hello", "/undo", "again", "/exit"),
        print_fn=lines.append,
    )
    assert code == 0
    files = list(isolated_sessions.glob("*.jsonl"))
    assert len(files) >= 2, "undo writes the truncated transcript to a new file"
    containing_again = [
        path
        for path in files
        if any(
            getattr(message, "content", None) == "again"
            for message in Session(path).load()
        )
    ]
    assert containing_again, "the post-undo turn must persist somewhere"
    # The new file is the continuation: it never carries the undone prompt.
    assert all(
        not any(
            getattr(message, "content", None) == "hello"
            for message in Session(path).load()
        )
        for path in containing_again
    )


async def memory_services(runtime_factory, tmp_path):
    """Same helper as tests/test_app_commands.py: store on an empty tmp library."""
    from tests.test_app_commands import memory_services as base
    return await base(runtime_factory, tmp_path)


async def test_memory_manager_screen_renders_index_lines(runtime_factory, tmp_path):
    services = await memory_services(runtime_factory, tmp_path)
    entry = services.runtime.memory_store.write_entry(title="选型", body="B", source="test")
    lines: list[str] = []
    _render(services, CommandResult(screen="memory-manager"), lines)
    assert any(entry.id in line and "选型" in line for line in lines)


async def test_memory_manager_screen_empty_library_prints_empty_message(runtime_factory, tmp_path):
    services = await memory_services(runtime_factory, tmp_path)
    lines: list[str] = []
    _render(services, CommandResult(screen="memory-manager"), lines)
    assert any("no memory notes" in line for line in lines)


async def test_memory_add_screen_runs_editor_and_saves_ui_entry(runtime_factory, tmp_path, monkeypatch):
    services = await memory_services(runtime_factory, tmp_path)
    monkeypatch.setattr(repl_module, "run_editor",
                        lambda initial: "title: 新条目\n\n正文内容")
    lines: list[str] = []
    _render(services, CommandResult(screen="memory-add", target="新条目"), lines)
    saved = services.runtime.memory_store.list_entries()
    assert len(saved) == 1 and saved[0].title == "新条目" and saved[0].source == "ui"
    assert saved[0].body == "正文内容"


async def test_memory_add_screen_without_editor_writes_nothing(runtime_factory, tmp_path, monkeypatch):
    services = await memory_services(runtime_factory, tmp_path)
    monkeypatch.setattr(repl_module, "run_editor", lambda initial: None)
    lines: list[str] = []
    _render(services, CommandResult(screen="memory-add", target=""), lines)
    assert services.runtime.memory_store.list_entries() == []
    assert any("$VISUAL" in line for line in lines)


async def test_memory_edit_screen_updates_existing_entry(runtime_factory, tmp_path, monkeypatch):
    services = await memory_services(runtime_factory, tmp_path)
    entry = services.runtime.memory_store.write_entry(title="旧", body="旧正文", source="agent")
    monkeypatch.setattr(repl_module, "run_editor",
                        lambda initial: "title: 新标题\n\n新正文")
    lines: list[str] = []
    _render(services, CommandResult(screen="memory-edit", target=entry.id), lines)
    updated = services.runtime.memory_store.read_entry(entry.id)
    assert updated.title == "新标题" and updated.body == "新正文"
    assert updated.source == "agent"  # update keeps the original source


def test_run_editor_roundtrip_through_env_editor(tmp_path, monkeypatch):
    script = tmp_path / "fake-editor.sh"
    script.write_text('#!/bin/sh\nprintf "appended" >> "$1"\n')
    script.chmod(0o755)
    monkeypatch.setenv("EDITOR", str(script))
    monkeypatch.delenv("VISUAL", raising=False)
    from zenagent.cli.editor import run_editor
    assert run_editor("title: t\n\n").endswith("appended")


def test_run_editor_without_env_returns_none(monkeypatch):
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.delenv("EDITOR", raising=False)
    from zenagent.cli.editor import run_editor
    assert run_editor("x") is None
