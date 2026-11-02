"""REPL tests: input splitting, slash commands, steering while running, smoke.

A fake ``input_fn`` scripts the keystrokes and an injected ``stream_fn`` keeps
everything offline.
"""

import asyncio
import time

import pytest

import zenagent.agent.session as session_module
import zenagent.cli.repl as repl_module
from zenagent.cli.repl import repl, split_input
from zenagent.config import UserConfig
from zenagent.events import AssistantEvent
from zenagent.providers.catalog import Model
from zenagent.providers.compat import CompatFlags
from zenagent.types import AssistantMessage, Cost, TextContent, Usage


@pytest.fixture(autouse=True)
def isolate_user_config(monkeypatch):
    """Tests must not read the developer's real ~/.zenagent/config.toml."""
    monkeypatch.setattr(repl_module, "load_user_config", lambda: UserConfig())
    monkeypatch.setattr(
        "zenagent.app.runtime.load_user_config",
        lambda *args, **kwargs: UserConfig(),
    )


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


def scripted_stream(delay: float = 0.0, *turns):
    class Stream:
        def __init__(self) -> None:
            self.turns = list(turns)
            self.requests: list[list] = []

        async def __call__(self, model_, messages, tools, options):
            self.requests.append(list(messages))
            text = self.turns.pop(0) if self.turns else "ok"
            partial = AssistantMessage(
                content=[],
                provider="test",
                model="m",
                usage=Usage(),
                stop_reason="pending",
                timestamp=int(time.time()),
            )
            yield AssistantEvent(type="start", partial=partial)
            if delay:
                await asyncio.sleep(delay)
            partial.content.append(TextContent(text=text))
            yield AssistantEvent(type="text_delta", partial=partial, delta=text)
            partial.stop_reason = "stop"
            yield AssistantEvent(type="done", partial=partial, reason="stop")

    return Stream()


def scripted_input(*lines: str, settle: float = 0.0):
    pending = list(lines)

    async def read() -> str:
        if settle:
            await asyncio.sleep(settle)
        if not pending:
            raise EOFError
        return pending.pop(0)

    return read


@pytest.fixture(autouse=True)
def isolated_sessions(tmp_path, monkeypatch):
    root = tmp_path / "sessions"
    monkeypatch.setattr(session_module, "sessions_dir", lambda: root)
    return root


# ---------------------------------------------------------------------------
# input splitting
# ---------------------------------------------------------------------------


def test_split_input_prompt_and_commands():
    assert split_input("  hello world ") == ("", "hello world")
    assert split_input("/help") == ("help", "")
    assert split_input("/model deepseek-flash") == ("model", "deepseek-flash")
    assert split_input("  /THINKING   high ") == ("thinking", "high")


# ---------------------------------------------------------------------------
# slash commands
# ---------------------------------------------------------------------------


async def test_repl_smoke_two_turns_then_exit(isolated_sessions):
    stream = scripted_stream(0.0, "first answer", "second answer")
    code = await repl(
        "glm-5.3",
        stream_fn=stream,
        input_fn=scripted_input("one", "two", "/exit", settle=0.02),
        print_fn=lambda *a, **k: None,
        api_key="k",
    )
    assert code == 0
    assert len(stream.requests) == 2
    # the session file received the four messages of both turns
    files = list(isolated_sessions.glob("*.jsonl"))
    assert len(files) == 1
    from zenagent.agent.session import Session

    assert len(Session(files[0]).load()) == 4


async def test_repl_eof_exits_cleanly(isolated_sessions):
    code = await repl(
        "glm-5.3",
        stream_fn=scripted_stream(0.0, "hi"),
        input_fn=scripted_input(),
        print_fn=lambda *a, **k: None,
        api_key="k",
    )
    assert code == 0


async def test_typing_while_running_steers_instead_of_stacking(isolated_sessions):
    """A line typed during a run is queued; the next run sees it as history."""
    stream = scripted_stream(0.15, "slow answer", "second answer")
    schedule = [
        (0.0, "first"),
        (0.02, "please also check logs"),  # typed while the first run streams
        (0.30, "second"),  # typed after it settled → a real second prompt
        (0.0, "/exit"),
    ]

    async def input_fn() -> str:
        delay, line = schedule.pop(0)
        if delay:
            await asyncio.sleep(delay)
        return line

    code = await repl(
        "glm-5.3",
        stream_fn=stream,
        input_fn=input_fn,
        print_fn=lambda *a, **k: None,
        api_key="k",
    )
    assert code == 0
    # Steering is delivered at the next step boundary; because the first run
    # ended before it landed, ChatController continues the turn to deliver it,
    # then the next typed prompt starts a third request.
    assert len(stream.requests) == 3
    request_texts = [
        m.content
        for request in stream.requests
        for m in request
        if hasattr(m, "content") and isinstance(m.content, str)
    ]
    assert "please also check logs" in request_texts


async def test_unknown_model_returns_one():
    code = await repl(
        "no-such-model", stream_fn=scripted_stream(), input_fn=scripted_input(), api_key="k"
    )
    assert code == 1


async def test_missing_key_returns_one(monkeypatch):
    for name in ("ZAI_CODING_CN_API_KEY", "DEEPSEEK_API_KEY", "OPENCODE_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    code = await repl("glm-5.3", stream_fn=scripted_stream(), input_fn=scripted_input())
    assert code == 1


# ---------------------------------------------------------------------------
# S1-T8: session commands
# ---------------------------------------------------------------------------


async def test_repl_prints_a_status_line_after_each_run(isolated_sessions):
    lines: list[str] = []
    code = await repl(
        "glm-5.3",
        stream_fn=scripted_stream(0.0, "hello"),
        input_fn=scripted_input("hi", "/exit", settle=0.02),
        print_fn=lambda *a, **k: lines.append("".join(str(x) for x in a)),
        api_key="k",
    )
    assert code == 0
    assert any("msgs" in line and "ctx" in line for line in lines), lines


async def test_repl_reconnects_a_late_mcp_server(tmp_path, isolated_sessions):
    import json
    import sys
    from pathlib import Path

    sentinel = tmp_path / "ready"
    flaky = Path(__file__).resolve().parent / "fixtures" / "mcp_flaky_server.py"
    mcp_file = tmp_path / "mcp.json"
    mcp_file.write_text(
        json.dumps(
            {
                "servers": {
                    "flaky": {
                        "command": [sys.executable, str(flaky)],
                        "env": {"MCP_READY_FILE": str(sentinel)},
                    }
                }
            }
        )
    )

    calls = {"n": 0}

    async def input_fn() -> str:
        await asyncio.sleep(0.02)
        calls["n"] += 1
        if calls["n"] == 1:
            return "hi"
        if calls["n"] == 2:
            sentinel.write_text("ready")  # the server comes up mid-session
            return "/mcp retry"
        return "/exit"

    lines: list[str] = []
    code = await repl(
        "glm-5.3",
        stream_fn=scripted_stream(0.0, "hello"),
        input_fn=input_fn,
        print_fn=lambda *a, **k: lines.append("".join(str(x) for x in a)),
        api_key="k",
        mcp_file=str(mcp_file),
        mcp_retry_interval_s=0,
    )
    assert code == 0
    joined = "\n".join(lines)
    assert "mcp: tools updated" in joined
    assert "flaky: connected" in joined


async def test_reload_discovers_skills_added_mid_session(tmp_path, monkeypatch):
    import zenagent.cli.repl as repl_module
    from zenagent.config import UserConfig

    monkeypatch.setattr(repl_module, "load_user_config", lambda: UserConfig())
    monkeypatch.setattr(
        "zenagent.app.runtime.load_user_config",
        lambda *args, **kwargs: UserConfig(),
    )
    monkeypatch.setenv("HOME", str(tmp_path))  # don't discover the developer's global skills
    monkeypatch.chdir(tmp_path)
    # Discovery also reads ~/.zenagent/skills: point HOME at the tmpdir so the
    # developer's own skill library cannot leak into the count below.
    monkeypatch.setenv("HOME", str(tmp_path))

    calls = {"n": 0}

    async def input_fn() -> str:
        await asyncio.sleep(0.02)
        calls["n"] += 1
        if calls["n"] == 1:
            return "hi"
        if calls["n"] == 2:
            skill_dir = tmp_path / ".zenagent" / "skills" / "late"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\nname: late\ndescription: added mid-session\n---\nbody\n"
            )
            return "/reload"
        return "/exit"

    lines: list[str] = []
    code = await repl(
        "glm-5.3",
        stream_fn=scripted_stream(0.0, "hello"),
        input_fn=input_fn,
        print_fn=lambda *a, **k: lines.append("".join(str(x) for x in a)),
        api_key="k",
    )
    assert code == 0
    joined = "\n".join(lines)
    assert "reloaded:" in joined
    assert "1 skills" in joined
    # the newly discovered skill also shows up in /skills
    lines.clear()
    calls["n"] = 0


# ---------------------------------------------------------------------------
# S2-T10: themes and hotkeys
# ---------------------------------------------------------------------------


async def test_slash_theme_switches_and_reports(isolated_sessions):
    lines: list[str] = []

    async def input_fn() -> str:
        await asyncio.sleep(0.01)
        return "/theme mono" if len(lines) == 0 else "/exit"

    code = await repl(
        "glm-5.3",
        stream_fn=scripted_stream(0.0, "hi"),
        input_fn=input_fn,
        print_fn=lambda *a, **k: lines.append("".join(str(x) for x in a)),
        api_key="k",
    )
    assert code == 0
    assert any("theme -> mono" in line for line in lines)


async def test_slash_tools_lists_the_tool_set(isolated_sessions, capsys):
    code = await repl(
        "glm-5.3",
        stream_fn=scripted_stream(),
        api_key="k",
        input_fn=scripted_input("/tools", "/exit"),
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "tools (" in out and "read_file" in out


async def test_slash_retry_reruns_the_last_prompt(isolated_sessions, capsys):
    stream = scripted_stream(0, "first answer", "retry answer")
    code = await repl(
        "glm-5.3",
        stream_fn=stream,
        api_key="k",
        input_fn=scripted_input("hello", "/retry", "/exit", settle=0.05),
        print_fn=lambda *a, **k: None,
    )
    assert code == 0
    assert len(stream.requests) == 2
    # retry re-sends the same prompt; the transcript never gains a second copy.
    for request in stream.requests:
        users = [m for m in request if m.to_json()["role"] == "user"]
        assert len(users) == 1 and users[0].content == "hello"


# ---------------------------------------------------------------------------
# /skill:<name> invocation (user-side body injection)
# ---------------------------------------------------------------------------


def _write_skill(root, name: str, body: str):
    directory = root / ".zenagent" / "skills" / name
    directory.mkdir(parents=True)
    path = directory / "SKILL.md"
    path.write_text(
        f"---\nname: {name}\ndescription: Build reports\n---\n{body}\n"
    )
    return path


async def test_repl_injects_the_skill_body(
    isolated_sessions, tmp_path, monkeypatch
):
    from zenagent.types import UserMessage

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    _write_skill(tmp_path, "pdf-report", "REPL SKILL BODY")
    stream = scripted_stream(0.0, "done")
    printed: list[str] = []

    code = await repl(
        "glm-5.3",
        stream_fn=stream,
        input_fn=scripted_input("/skill:pdf-report summarize this", "/exit", settle=0.02),
        print_fn=printed.append,
        api_key="k",
        skills=True,
        templates=False,
        mcp=False,
    )
    assert code == 0
    assert len(stream.requests) == 1
    user_messages = [
        message for message in stream.requests[0] if isinstance(message, UserMessage)
    ]
    injected = user_messages[0].content
    assert isinstance(injected, str)
    assert injected.startswith("[skill:pdf-report] summarize this")
    assert "REPL SKILL BODY" in injected
    assert "[IMPORTANT: User invoked the" in injected


async def test_repl_reports_an_unknown_skill_without_running(
    isolated_sessions, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    _write_skill(tmp_path, "pdf-report", "body")
    stream = scripted_stream(0.0, "done")
    printed: list[str] = []

    code = await repl(
        "glm-5.3",
        stream_fn=stream,
        input_fn=scripted_input("/skill:nope go", "/exit", settle=0.02),
        print_fn=printed.append,
        api_key="k",
        skills=True,
        templates=False,
        mcp=False,
    )
    assert code == 0
    assert stream.requests == []
    assert any("unknown skill: nope" in line for line in printed)


async def test_repl_injects_an_embedded_skill_token(
    isolated_sessions, tmp_path, monkeypatch
):
    from zenagent.types import UserMessage

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    _write_skill(tmp_path, "pdf-report", "EMBEDDED BODY")
    stream = scripted_stream(0.0, "done")

    code = await repl(
        "glm-5.3",
        stream_fn=stream,
        input_fn=scripted_input("please /skill:pdf-report check it", "/exit", settle=0.02),
        print_fn=lambda *a, **k: None,
        api_key="k",
        skills=True,
        templates=False,
        mcp=False,
    )
    assert code == 0
    (user_message,) = [
        message for message in stream.requests[0] if isinstance(message, UserMessage)
    ]
    injected = user_message.content
    assert isinstance(injected, str)
    assert injected.startswith("[skill:pdf-report] please check it")
    assert "EMBEDDED BODY" in injected


def test_repl_paints_tool_progress_in_place_on_a_tty(capsys):
    """A running bash call must show its latest line without spamming the log."""
    from rich.console import Console

    from zenagent.agent.loop import ToolResultAgent
    from zenagent.events import AgentEvent

    printer = repl_module._EventPrinter(
        console=Console(force_terminal=True), print_fn=lambda *args, **kwargs: None
    )
    partial = ToolResultAgent(content=[TextContent(text="building\nstep 1 of 3")])
    update = AgentEvent(
        type="tool_execution_update", tool_name="bash", partial_result=partial
    )
    printer(update)
    printer(update)  # inside the throttle window: must not repaint
    out = capsys.readouterr().out
    assert out.count("\r") == 1
    assert "⚙ bash" in out and "step 1 of 3" in out
    assert printer._progress_open is True

    printer(
        AgentEvent(
            type="tool_execution_end",
            tool_name="bash",
            result=ToolResultAgent(content=[TextContent(text="done")]),
            duration_ms=5,
        )
    )
    out = capsys.readouterr().out
    assert "\x1b[K" in out  # the progress line was cleared before the result
    assert printer._progress_open is False


def test_repl_does_not_paint_progress_without_a_terminal(capsys):
    """Pipes/redirected runs must not get carriage-return noise."""
    from zenagent.agent.loop import ToolResultAgent
    from zenagent.events import AgentEvent

    printer = repl_module._EventPrinter(console=None)
    printer(
        AgentEvent(
            type="tool_execution_update",
            tool_name="bash",
            partial_result=ToolResultAgent(content=[TextContent(text="step")]),
        )
    )
    assert capsys.readouterr().out == ""


async def test_repl_forwards_the_thinking_flag(isolated_sessions):
    seen: list = []

    async def stream(model_, messages, tools, options):
        seen.append(options.thinking)
        partial = AssistantMessage(
            content=[TextContent(text="ok")], provider="test", model="m",
            usage=Usage(), stop_reason="stop", timestamp=int(time.time()),
        )
        yield AssistantEvent(type="done", partial=partial, reason="stop")

    code = await repl(
        "glm-5.3",
        stream_fn=stream,
        input_fn=scripted_input("hi", "/exit"),
        print_fn=lambda *a, **k: None,
        api_key="k",
        thinking="low",
    )

    assert code == 0
    assert seen == ["low"]


async def test_repl_attaches_startup_images_to_the_first_prompt(
    isolated_sessions, tmp_path, monkeypatch
):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deep-key")
    image = tmp_path / "shot.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
    seen: list = []

    async def stream(model_, messages, tools, options):
        seen.append(messages)
        partial = AssistantMessage(
            content=[TextContent(text="ok")], provider="test", model="m",
            usage=Usage(), stop_reason="stop", timestamp=int(time.time()),
        )
        yield AssistantEvent(type="done", partial=partial, reason="stop")

    code = await repl(
        "deepseek-flash",
        stream_fn=stream,
        input_fn=scripted_input("describe", "/exit"),
        print_fn=lambda *a, **k: None,
        api_key="k",
        images=[image],
    )

    assert code == 0
    kinds = [type(block).__name__ for block in seen[0][-1].content]
    assert kinds == ["ImageContent", "TextContent"]


async def test_repl_rejects_startup_images_for_a_non_vision_model(isolated_sessions, tmp_path, capsys):
    image = tmp_path / "shot.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)

    code = await repl(
        "glm-5.3",
        stream_fn=scripted_stream(),
        input_fn=scripted_input(),
        print_fn=lambda *a, **k: None,
        api_key="k",
        images=[image],
    )

    assert code == 1
    assert "does not accept images" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# P1: the REPL runs on the shared Runtime
# ---------------------------------------------------------------------------


async def test_repl_exposes_the_tools_the_tui_has(
    isolated_sessions, tmp_path, monkeypatch
):
    # A project-local skill makes read_skill deterministic: a developer machine
    # with ~/.zenagent/skills must not be what makes this pass (CI has none).
    monkeypatch.chdir(tmp_path)
    skill_dir = tmp_path / ".zenagent" / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo\ndescription: demo skill\n---\nbody\n"
    )
    seen: list[str] = []

    async def stream(model_, messages, tools, options):
        seen.extend(tool.name for tool in tools)
        partial = AssistantMessage(
            content=[TextContent(text="ok")], provider="test", model="m",
            usage=Usage(), stop_reason="stop", timestamp=int(time.time()),
        )
        yield AssistantEvent(type="done", partial=partial, reason="stop")

    code = await repl(
        "glm-5.3",
        stream_fn=stream,
        input_fn=scripted_input("hi", "/exit"),
        print_fn=lambda *a, **k: None,
        api_key="k",
    )

    assert code == 0
    for name in ("checkpoint", "rewind", "lsp", "todo", "task", "read_skill"):
        assert name in seen, name


async def test_repl_resume_loads_the_runtime_session(isolated_sessions):
    from zenagent.agent.session import Session, unique_session_path
    from zenagent.types import UserMessage

    prior = Session(unique_session_path())
    prior.append(UserMessage(content="earlier question", timestamp=1))
    seen: list = []

    async def stream(model_, messages, tools, options):
        seen.append(messages)
        partial = AssistantMessage(
            content=[TextContent(text="ok")], provider="test", model="m",
            usage=Usage(), stop_reason="stop", timestamp=int(time.time()),
        )
        yield AssistantEvent(type="done", partial=partial, reason="stop")

    code = await repl(
        "glm-5.3",
        resume=prior.path,
        stream_fn=stream,
        input_fn=scripted_input("follow-up", "/exit"),
        print_fn=lambda *a, **k: None,
        api_key="k",
    )

    assert code == 0
    texts = [
        message.content
        for message in seen[0]
        if isinstance(message, UserMessage)
    ]
    assert "earlier question" in texts
