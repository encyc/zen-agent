"""REPL tests: input splitting, slash commands, steering while running, smoke.

A fake ``input_fn`` scripts the keystrokes and an injected ``stream_fn`` keeps
everything offline.
"""

import asyncio
import time

import pytest

import pyagent.agent.session as session_module
import pyagent.cli.repl as repl_module
from pyagent.agent import Agent
from pyagent.cli.repl import handle_slash, repl, split_input
from pyagent.config import UserConfig
from pyagent.events import AssistantEvent
from pyagent.providers.catalog import Model
from pyagent.providers.compat import CompatFlags
from pyagent.types import AssistantMessage, Cost, TextContent, Usage


@pytest.fixture(autouse=True)
def isolate_user_config(monkeypatch):
    """Tests must not read the developer's real ~/.pyagent/config.toml."""
    monkeypatch.setattr(repl_module, "load_user_config", lambda: UserConfig())


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
            self.turns = [list(t) for t in turns]
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


async def test_slash_exit_signals_the_repl_to_stop(capsys):
    agent = Agent(model(), "s", api_key="k")
    assert await handle_slash(agent, "exit", "") == "exit"


async def test_slash_model_switches_and_reports(capsys):
    agent = Agent(model(), "s", api_key="k")
    await handle_slash(agent, "model", "")
    assert "glm-5.3" in capsys.readouterr().out

    await handle_slash(agent, "model", "deepseek-flash")
    assert agent.model.id == "deepseek-flash"
    assert "deepseek-flash" in capsys.readouterr().out


async def test_slash_model_rejects_unknown_id(capsys):
    agent = Agent(model(), "s", api_key="k")
    await handle_slash(agent, "model", "nope")
    assert agent.model.id == "glm-5.3"
    assert "unknown model" in capsys.readouterr().out


async def test_slash_thinking_validates_levels(capsys):
    agent = Agent(model(), "s", api_key="k")
    await handle_slash(agent, "thinking", "high")
    assert agent.thinking == "high"

    await handle_slash(agent, "thinking", "banana")
    assert agent.thinking == "high"  # unchanged
    assert "usage:" in capsys.readouterr().out


async def test_slash_clear_resets_the_transcript(capsys):
    agent = Agent(model(), "s", api_key="k")
    agent.replace_messages([__import__("pyagent.types", fromlist=["UserMessage"]).UserMessage(content="x", timestamp=1)])
    await handle_slash(agent, "clear", "")
    assert agent.messages == []


async def test_unknown_slash_command_is_reported(capsys):
    agent = Agent(model(), "s", api_key="k")
    await handle_slash(agent, "banana", "")
    assert "unknown command" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# REPL loop
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
    from pyagent.agent.session import Session

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
    stream = scripted_stream(0.06, "slow answer", "second answer")
    code = await repl(
        "glm-5.3",
        stream_fn=stream,
        input_fn=scripted_input("first", "please also check logs", "second", "/exit", settle=0.03),
        print_fn=lambda *a, **k: None,
        api_key="k",
    )
    assert code == 0
    assert len(stream.requests) == 2
    second_request_texts = [
        m.content for m in stream.requests[1] if hasattr(m, "content") and isinstance(m.content, str)
    ]
    assert "please also check logs" in second_request_texts


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


async def test_slash_sessions_lists_and_marks_the_current(isolated_sessions, capsys):
    from pyagent.agent.session import Session
    from pyagent.types import UserMessage

    isolated_sessions.mkdir(parents=True)
    current = Session(isolated_sessions / "current.jsonl")
    current.append(UserMessage(content="hello", timestamp=1))
    Session(isolated_sessions / "other.jsonl").append(UserMessage(content="x", timestamp=1))

    agent = Agent(model(), "s", api_key="k")
    await handle_slash(agent, "sessions", "", session=current)
    out = capsys.readouterr().out
    assert "current.jsonl" in out and "other.jsonl" in out
    assert "*" in out


async def test_slash_fork_switches_the_transcript(isolated_sessions, capsys):
    from pyagent.agent.session import Session
    from pyagent.types import AssistantMessage, TextContent, Usage, UserMessage

    isolated_sessions.mkdir(parents=True)
    session = Session(isolated_sessions / "s.jsonl")
    session.append(UserMessage(content="first", timestamp=1))
    session.append(AssistantMessage(content=[TextContent(text="a1")], provider="p",
                                    model="m", usage=Usage(), stop_reason="stop", timestamp=2))
    session.append(UserMessage(content="second", timestamp=3))

    switched: list[Session] = []
    agent = Agent(model(), "s", api_key="k")
    await handle_slash(
        agent, "fork", "2", session=session, switch_session=switched.append
    )
    assert switched, "fork must switch the active session"
    assert [m.content for m in agent.messages if isinstance(m, UserMessage)] == ["first", "second"]


async def test_slash_fork_rejects_bad_arguments(isolated_sessions, capsys):
    from pyagent.agent.session import Session

    isolated_sessions.mkdir(parents=True)
    session = Session(isolated_sessions / "s.jsonl")
    agent = Agent(model(), "s", api_key="k")
    await handle_slash(agent, "fork", "x", session=session)
    assert "usage: /fork" in capsys.readouterr().out


async def test_slash_import_loads_a_session(isolated_sessions, capsys):
    from pyagent.agent.session import Session
    from pyagent.types import UserMessage

    isolated_sessions.mkdir(parents=True)
    path = isolated_sessions / "imported.jsonl"
    Session(path).append(UserMessage(content="from disk", timestamp=1))

    switched: list[Session] = []
    agent = Agent(model(), "s", api_key="k")
    await handle_slash(
        agent, "import", str(path), switch_session=switched.append
    )
    assert [m.content for m in agent.messages] == ["from disk"]
    assert switched and switched[0].path == path


async def test_slash_undo_drops_the_last_turn_into_a_new_file(isolated_sessions, capsys):
    from pyagent.agent.session import Session
    from pyagent.types import AssistantMessage, TextContent, Usage, UserMessage

    isolated_sessions.mkdir(parents=True)
    session = Session(isolated_sessions / "s.jsonl")
    session.append(UserMessage(content="q1", timestamp=1))
    session.append(AssistantMessage(content=[TextContent(text="a1")], provider="p",
                                    model="m", usage=Usage(), stop_reason="stop", timestamp=2))
    session.append(UserMessage(content="q2", timestamp=3))
    session.append(AssistantMessage(content=[TextContent(text="a2")], provider="p",
                                    model="m", usage=Usage(), stop_reason="stop", timestamp=4))
    before = session.path.read_bytes()

    switched: list[Session] = []
    agent = Agent(model(), "s", api_key="k")
    agent.replace_messages(session.load())
    await handle_slash(agent, "undo", "", session=session, switch_session=switched.append)

    assert [m.content for m in agent.messages if isinstance(m, UserMessage)] == ["q1"]
    assert switched and switched[0].path != session.path
    assert session.path.read_bytes() == before  # original untouched
    assert "undid" in capsys.readouterr().out


async def test_slash_undo_with_nothing_to_drop(isolated_sessions, capsys):
    from pyagent.agent.session import Session

    isolated_sessions.mkdir(parents=True)
    agent = Agent(model(), "s", api_key="k")
    await handle_slash(agent, "undo", "", session=Session(isolated_sessions / "s.jsonl"))
    assert "nothing to undo" in capsys.readouterr().out


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


async def test_slash_session_prints_state(isolated_sessions, capsys):
    agent = Agent(model(), "s", api_key="k")
    await handle_slash(agent, "session", "")
    out = capsys.readouterr().out
    assert "glm-5.3" in out and "msgs" in out


def vision_model() -> Model:
    m = model()
    m.supports_vision = True
    return m


async def test_slash_image_attaches_only_for_vision_models(tmp_path, capsys):
    image = tmp_path / "pic.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)

    non_vision = Agent(model(), "s", api_key="k")
    await handle_slash(non_vision, "image", str(image), images=[])
    assert "does not accept images" in capsys.readouterr().out

    pending: list = []
    vision = Agent(vision_model(), "s", api_key="k")
    await handle_slash(vision, "image", str(image), images=pending)
    assert len(pending) == 1 and pending[0].mime_type == "image/png"
    assert "attached" in capsys.readouterr().out


async def test_slash_images_lists_and_clears(tmp_path, capsys):
    from pyagent.cli.images import read_image_argument

    image = tmp_path / "pic.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
    pending = [read_image_argument(image)]
    agent = Agent(vision_model(), "s", api_key="k")

    await handle_slash(agent, "images", "", images=pending)
    assert "image/png" in capsys.readouterr().out
    await handle_slash(agent, "images", "clear", images=pending)
    assert pending == []


# ---------------------------------------------------------------------------
# S2-T3: late MCP server becomes usable in the same session
# ---------------------------------------------------------------------------


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


async def test_slash_skills_lists_them(capsys):
    from pathlib import Path

    from pyagent.skills import Skill

    agent = Agent(model(), "s", api_key="k")
    await handle_slash(
        agent,
        "skills",
        "",
        skill_list=[Skill(name="deploy", description="ship it", path=Path("/x"), body="")],
    )
    out = capsys.readouterr().out
    assert "deploy" in out and "ship it" in out


async def test_slash_skills_with_none_configured(capsys):
    agent = Agent(model(), "s", api_key="k")
    await handle_slash(agent, "skills", "", skill_list=[])
    assert "no skills found" in capsys.readouterr().out
