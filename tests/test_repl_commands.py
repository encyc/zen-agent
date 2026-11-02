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
from zenagent.app.commands import build_default_commands
from zenagent.cli.repl import render_command_result, render_help, repl
from zenagent.config import UserConfig
from zenagent.events import AssistantEvent
from zenagent.providers.catalog import Model
from zenagent.providers.compat import CompatFlags
from zenagent.types import AssistantMessage, Cost, TextContent, Usage


@pytest.fixture(autouse=True)
def isolate_config(monkeypatch):
    monkeypatch.setattr(repl_module, "load_user_config", lambda: UserConfig())
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


async def test_repl_compact_continues_in_the_summary_session(isolated_sessions):
    from zenagent.agent.session import Session

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
    compacted = [path for path in files if "-compact" in path.name]
    assert compacted, "compaction must write a summary session"
    # The post-compaction turn landed in the summary file, not the old one.
    texts = [
        message.content
        for message in Session(compacted[0]).load()
        if isinstance(message.content, str)
    ]
    assert "next" in texts


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
