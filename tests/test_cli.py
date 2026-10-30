"""Headless CLI tests: model resolution, streaming output, permission gate.

The network is replaced by a fake ``stream_fn`` injected through the
``_build_agent`` seam, so ``main()`` itself is exercised end to end.
"""

import time

import pytest

import zenagent.cli.main as cli_main
from zenagent.config import UserConfig


@pytest.fixture(autouse=True)
def isolate_user_config(monkeypatch):
    """Tests must not read the developer's real ~/.zenagent/config.toml."""
    monkeypatch.setattr(cli_main, "load_user_config", lambda: UserConfig())
from zenagent.agent import Agent
from zenagent.cli.permissions import READ_ONLY_TOOLS, PermissionGate
from zenagent.events import AssistantEvent
from zenagent.providers.catalog import load_catalog
from zenagent.types import AssistantMessage, TextContent, ToolCall, Usage


def fake_stream(text: str = "Hello from fake"):
    """A StreamFn that emits one text block and stops."""

    async def stream(model, messages, tools, options):
        partial = AssistantMessage(
            content=[],
            provider=model.provider,
            model=model.id,
            usage=Usage(),
            stop_reason="pending",
            timestamp=int(time.time()),
        )
        yield AssistantEvent(type="start", partial=partial)
        partial.content.append(TextContent(text=text))
        yield AssistantEvent(
            type="text_delta", partial=partial, delta=text, content_index=0
        )
        partial.stop_reason = "stop"
        yield AssistantEvent(type="done", partial=partial, reason="stop")

    return stream


def failing_stream(message: str = "boom"):
    async def stream(model, messages, tools, options):
        partial = AssistantMessage(
            content=[],
            provider=model.provider,
            model=model.id,
            usage=Usage(),
            stop_reason="pending",
            timestamp=int(time.time()),
        )
        yield AssistantEvent(type="start", partial=partial)
        partial.stop_reason = "error"
        partial.error_message = message
        yield AssistantEvent(type="error", partial=partial, reason="error")

    return stream


# ---------------------------------------------------------------------------
# Model resolution
# ---------------------------------------------------------------------------


def test_resolve_model_by_id_and_default():
    catalog = load_catalog()
    assert cli_main.resolve_model(catalog, "deepseek-flash").id == "deepseek-flash"
    default = cli_main.resolve_model(catalog, None)
    assert default.default is True
    assert default.id == "glm-5.3"


def test_resolve_model_rejects_unknown_id():
    with pytest.raises(KeyError):
        cli_main.resolve_model(load_catalog(), "no-such-model")


# ---------------------------------------------------------------------------
# main() end to end (fake stream via the _build_agent seam)
# ---------------------------------------------------------------------------


def _patch_agent(monkeypatch, stream, captured: dict):
    """Replace _build_agent with one that injects ``stream``, recording args."""

    def build(
        model,
        api_key,
        thinking,
        gate,
        stream_fn=None,
        tools=None,
        session_id=None,
        transform_context=None,
        max_turns=None,
        max_cost=None,
        retries=0, context_files=True,
        extra_prompt="",
    ):
        captured.update(model=model, thinking=thinking, gate=gate, tools=tools)
        return Agent(
            model=model,
            system_prompt="test",
            api_key=api_key,
            thinking=thinking,
            stream_fn=stream,
            before_tool_call=gate.before_tool_call,
            max_turns=max_turns,
            max_cost=max_cost,
        )

    monkeypatch.setattr(cli_main, "_build_agent", build)


def test_main_headless_streams_text_and_returns_zero(monkeypatch, capsys):
    monkeypatch.setenv("ZAI_CODING_CN_API_KEY", "test-key")
    captured: dict = {}
    _patch_agent(monkeypatch, fake_stream("Hello from fake"), captured)

    assert cli_main.main(["-p", "hi"]) == 0
    out = capsys.readouterr().out
    assert "Hello from fake" in out
    assert captured["model"].id == "glm-5.3"
    # a reasoning model defaults to thinking=high
    assert captured["thinking"] == "high"


def test_thinking_flag_is_forwarded(monkeypatch):
    monkeypatch.setenv("ZAI_CODING_CN_API_KEY", "test-key")
    captured: dict = {}
    _patch_agent(monkeypatch, fake_stream(), captured)

    assert cli_main.main(["-p", "hi", "--thinking", "off"]) == 0
    assert captured["thinking"] == "off"
    assert cli_main.main(["-p", "hi", "--thinking", "low"]) == 0
    assert captured["thinking"] == "low"


def test_main_returns_one_and_reports_error(monkeypatch, capsys):
    monkeypatch.setenv("ZAI_CODING_CN_API_KEY", "test-key")
    _patch_agent(monkeypatch, failing_stream("provider exploded"), {})

    assert cli_main.main(["-p", "hi"]) == 1
    captured = capsys.readouterr()
    assert "provider exploded" in captured.err


def test_missing_api_key_is_a_clean_error(monkeypatch, capsys):
    monkeypatch.delenv("ZAI_CODING_CN_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
    _patch_agent(monkeypatch, fake_stream(), {})

    assert cli_main.main(["-p", "hi"]) == 1
    err = capsys.readouterr().err
    assert "missing API key" in err
    assert "ZAI_CODING_CN_API_KEY" in err


def test_json_output_has_documented_fields(monkeypatch, capsys):
    monkeypatch.setenv("ZAI_CODING_CN_API_KEY", "test-key")
    _patch_agent(monkeypatch, fake_stream("json text"), {})

    assert cli_main.main(["-p", "hi", "--json"]) == 0
    import json

    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["ok"] is True
    assert payload["text"] == "json text"
    assert payload["messages"] == 2
    assert set(payload["usage"]) == {
        "input",
        "output",
        "cache_read",
        "cache_write",
        "cost",
    }


def test_no_prompt_enters_the_interactive_repl(monkeypatch):
    """Without -p the CLI hands control to the REPL (behavior of Task 17)."""
    import zenagent.cli.repl as repl_module

    async def fake_repl(**kwargs):
        return 0

    monkeypatch.setattr(repl_module, "repl", fake_repl)
    assert cli_main.main([]) == 0


# ---------------------------------------------------------------------------
# PermissionGate
# ---------------------------------------------------------------------------


async def test_read_only_tools_pass_without_confirmation():
    gate = PermissionGate(assume_yes=False, interactive=False)
    for name in READ_ONLY_TOOLS:
        allowed, reason = await gate.check(name, {})
        assert allowed is True and reason == ""


async def test_write_tool_denied_in_non_interactive_run_without_yes():
    gate = PermissionGate(assume_yes=False, interactive=False)
    allowed, reason = await gate.check("write_file", {"path": "a.txt"})
    assert allowed is False
    assert "non-interactive" in reason and "--yes" in reason


async def test_yes_allows_write_tools():
    gate = PermissionGate(assume_yes=True, interactive=False)
    allowed, reason = await gate.check("bash", {"command": "rm -rf /tmp/x"})
    assert allowed is True and reason == ""


async def test_interactive_answers_y_n_and_a(monkeypatch):
    answers = iter(["n", "y", "a", "y"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))
    gate = PermissionGate(assume_yes=False, interactive=True)

    assert (await gate.check("write_file", {}))[0] is False
    assert (await gate.check("write_file", {}))[0] is True
    assert (await gate.check("write_file", {}))[0] is True  # "a" remembers
    # remembered: no prompt consumed for this one
    assert (await gate.check("write_file", {}))[0] is True


async def test_before_tool_call_shapes_block_reason_for_the_loop():
    gate = PermissionGate(assume_yes=False, interactive=False)
    call = ToolCall(id="c1", name="write_file", arguments={"path": "a"})
    verdict = await gate.before_tool_call(call, {"path": "a"})
    assert verdict == {"block": True, "reason": "permission denied for 'write_file': "
                       "this is a non-interactive run without --yes"}
    read = ToolCall(id="c2", name="read_file", arguments={"path": "a"})
    assert await gate.before_tool_call(read, {"path": "a"}) is None


def test_keyboard_interrupt_returns_one_without_traceback(monkeypatch, capsys):
    monkeypatch.setenv("ZAI_CODING_CN_API_KEY", "test-key")

    async def interrupted(*_args, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli_main, "run_headless", interrupted)
    assert cli_main.main(["-p", "hi"]) == 1
    err = capsys.readouterr().err
    assert "interrupted" in err


def test_config_default_model_is_used(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    from zenagent.config import UserConfig

    monkeypatch.setattr(
        cli_main, "load_user_config", lambda: UserConfig(default_model="deepseek-flash")
    )
    captured: dict = {}
    _patch_agent(monkeypatch, fake_stream(), captured)

    assert cli_main.main(["-p", "hi"]) == 0
    assert captured["model"].id == "deepseek-flash"


def test_headless_resume_loads_the_previous_transcript(monkeypatch, tmp_path):
    import zenagent.agent.session as session_module
    from zenagent.agent.session import Session
    from zenagent.types import UserMessage

    monkeypatch.setenv("ZAI_CODING_CN_API_KEY", "test-key")
    monkeypatch.setattr(session_module, "sessions_dir", lambda: tmp_path)
    path = tmp_path / "s.jsonl"
    Session(path).append(UserMessage(content="earlier question", timestamp=1))

    built: dict = {}

    def build(
        model,
        api_key,
        thinking,
        gate,
        stream_fn=None,
        tools=None,
        session_id=None,
        transform_context=None,
        max_turns=None,
        max_cost=None,
        retries=0, context_files=True,
        extra_prompt="",
    ):
        agent = Agent(
            model=model,
            system_prompt="s",
            api_key=api_key,
            stream_fn=fake_stream("resumed ok"),
        )
        built["agent"] = agent
        return agent

    monkeypatch.setattr(cli_main, "_build_agent", build)

    assert cli_main.main(["-p", "next question", "--resume", str(path)]) == 0
    texts = [m.content for m in built["agent"].messages if isinstance(m, UserMessage)]
    assert "earlier question" in texts


def test_headless_run_persists_a_session(monkeypatch, tmp_path):
    import zenagent.agent.session as session_module
    from zenagent.agent.session import Session

    monkeypatch.setenv("ZAI_CODING_CN_API_KEY", "test-key")
    monkeypatch.setattr(session_module, "sessions_dir", lambda: tmp_path)
    _patch_agent(monkeypatch, fake_stream("persisted"), {})

    assert cli_main.main(["-p", "hi"]) == 0
    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) == 1
    assert len(Session(files[0]).load()) == 2  # user + assistant


# ---------------------------------------------------------------------------
# S1-T3: guardrails
# ---------------------------------------------------------------------------


def looping_tool_stream(tool_name: str = "ghost"):
    """Always asks for one more tool round, so the loop only a guardrail stops it."""

    async def stream(model, messages, tools, options):
        partial = AssistantMessage(
            content=[],
            provider=model.provider,
            model=model.id,
            usage=Usage(),
            stop_reason="pending",
            timestamp=int(time.time()),
        )
        yield AssistantEvent(type="start", partial=partial)
        call = ToolCall(id="c1", name=tool_name, arguments={})
        partial.content.append(call)
        yield AssistantEvent(type="toolcall_end", partial=partial, tool_call=call)
        partial.stop_reason = "tool_use"
        yield AssistantEvent(type="done", partial=partial, reason="tool_use")

    return stream


def test_guardrail_flags_reach_the_agent(monkeypatch):
    monkeypatch.setenv("ZAI_CODING_CN_API_KEY", "test-key")
    captured: dict = {}

    def build(model, api_key, thinking, gate, stream_fn=None, tools=None,
              session_id=None, transform_context=None, max_turns=None, max_cost=None,
              retries=0, context_files=True, extra_prompt=""):
        captured.update(max_turns=max_turns, max_cost=max_cost)
        return Agent(model=model, system_prompt="s", api_key=api_key, stream_fn=fake_stream())

    monkeypatch.setattr(cli_main, "_build_agent", build)
    assert cli_main.main(["-p", "hi", "--max-turns", "3", "--max-cost", "0.25"]) == 0
    assert captured == {"max_turns": 3, "max_cost": 0.25}


def test_max_turns_exhaustion_exits_one(monkeypatch, capsys):
    monkeypatch.setenv("ZAI_CODING_CN_API_KEY", "test-key")

    def build(model, api_key, thinking, gate, stream_fn=None, tools=None,
              session_id=None, transform_context=None, max_turns=None, max_cost=None,
              retries=0, context_files=True, extra_prompt=""):
        return Agent(
            model=model,
            system_prompt="s",
            api_key=api_key,
            stream_fn=looping_tool_stream(),
            max_turns=max_turns,
        )

    monkeypatch.setattr(cli_main, "_build_agent", build)
    assert cli_main.main(["-p", "hi", "--max-turns", "1"]) == 1
    err = capsys.readouterr().err
    assert "stopped early (max_turns)" in err


def test_json_reports_termination(monkeypatch, capsys):
    import json

    monkeypatch.setenv("ZAI_CODING_CN_API_KEY", "test-key")
    _patch_agent(monkeypatch, fake_stream("ok"), {})
    assert cli_main.main(["-p", "hi", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["termination"] == "stop"
    assert payload["ok"] is True


# ---------------------------------------------------------------------------
# S1-T12: image attachments
# ---------------------------------------------------------------------------


def recording_stream(text: str = "seen"):
    """Fake stream that records the messages it was handed."""

    class Stream:
        def __init__(self) -> None:
            self.requests: list[list] = []

        async def __call__(self, model, messages, tools, options):
            self.requests.append(list(messages))
            partial = AssistantMessage(
                content=[TextContent(text=text)],
                provider=model.provider,
                model=model.id,
                usage=Usage(),
                stop_reason="stop",
                timestamp=int(time.time()),
            )
            yield AssistantEvent(type="done", partial=partial, reason="stop")

    return Stream()


def _png(tmp_path):
    path = tmp_path / "shot.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
    return path


def test_image_flag_attaches_blocks_to_the_user_message(monkeypatch, tmp_path):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    stream = recording_stream()
    captured: dict = {}

    def build(model, api_key, thinking, gate, stream_fn=None, tools=None,
              session_id=None, transform_context=None, max_turns=None,
              max_cost=None, retries=0, context_files=True, extra_prompt=""):
        captured["model"] = model
        return Agent(model=model, system_prompt="s", api_key=api_key, stream_fn=stream)

    monkeypatch.setattr(cli_main, "_build_agent", build)
    assert cli_main.main(["-p", "what is this?", "-m", "deepseek-flash",
                          "--image", str(_png(tmp_path))]) == 0

    assert captured["model"].id == "deepseek-flash"
    user_message = stream.requests[0][1]
    assert isinstance(user_message.content, list)
    kinds = [type(block).__name__ for block in user_message.content]
    assert kinds == ["ImageContent", "TextContent"]  # image first, prompt last


def test_image_on_a_non_vision_model_fails_before_any_request(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    stream = recording_stream()

    def build(model, api_key, thinking, gate, stream_fn=None, tools=None,
              session_id=None, transform_context=None, max_turns=None,
              max_cost=None, retries=0, context_files=True, extra_prompt=""):
        return Agent(model=model, system_prompt="s", api_key=api_key, stream_fn=stream)

    monkeypatch.setattr(cli_main, "_build_agent", build)
    assert cli_main.main(["-p", "hi", "-m", "deepseek-v4-pro",
                          "--image", str(_png(tmp_path))]) == 1
    assert stream.requests == []  # no round trip wasted
    assert "does not accept images" in capsys.readouterr().err


def test_invalid_image_file_is_reported(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    bad = tmp_path / "not-an-image.png"
    bad.write_text("hello")
    _patch_agent(monkeypatch, fake_stream(), {})
    assert cli_main.main(["-p", "hi", "-m", "deepseek-flash", "--image", str(bad)]) == 1
    assert "does not look like" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# S2-T1: --cwd and --trace
# ---------------------------------------------------------------------------


def test_cwd_flag_changes_the_working_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("ZAI_CODING_CN_API_KEY", "test-key")
    import os

    seen: dict = {}

    def build(model, api_key, thinking, gate, stream_fn=None, tools=None,
              session_id=None, transform_context=None, max_turns=None,
              max_cost=None, retries=0, context_files=True, extra_prompt=""):
        seen["cwd"] = os.getcwd()
        return Agent(model=model, system_prompt="s", api_key=api_key, stream_fn=fake_stream())

    monkeypatch.setattr(cli_main, "_build_agent", build)
    original = os.getcwd()
    try:
        assert cli_main.main(["-p", "hi", "--cwd", str(tmp_path)]) == 0
    finally:
        os.chdir(original)
    assert seen["cwd"] == str(tmp_path)


def test_bad_cwd_is_rejected(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("ZAI_CODING_CN_API_KEY", "test-key")
    _patch_agent(monkeypatch, fake_stream(), {})
    assert cli_main.main(["-p", "hi", "--cwd", str(tmp_path / "missing")]) == 1
    assert "not a directory" in capsys.readouterr().err


def test_trace_flag_writes_jsonl(monkeypatch, tmp_path):
    import json

    monkeypatch.setenv("ZAI_CODING_CN_API_KEY", "test-key")
    _patch_agent(monkeypatch, looping_tool_stream(), {})
    trace_path = tmp_path / "run.jsonl"

    assert cli_main.main(["-p", "hi", "--max-turns", "1", "--trace", str(trace_path),
                          "--cwd", str(tmp_path)]) == 1
    lines = trace_path.read_text(encoding="utf-8").splitlines()
    assert lines
    payloads = [json.loads(line) for line in lines]
    assert any(p["type"] == "turn_start" for p in payloads)
    assert [p["type"] for p in payloads if p["type"].startswith("tool_")] == [
        "tool_start",
        "tool_end",
    ]


# ---------------------------------------------------------------------------
# S2-T3: MCP wiring
# ---------------------------------------------------------------------------


def _mcp_config(tmp_path, script: str, env: dict[str, str], name: str = "flaky"):
    import json as _json

    path = tmp_path / "mcp.json"
    path.write_text(
        _json.dumps(
            {
                "servers": {
                    name: {
                        "command": [__import__("sys").executable, script],
                        "env": env,
                    }
                }
            }
        )
    )
    return path


def test_headless_survives_a_down_mcp_server(monkeypatch, tmp_path, capsys):
    import sys as _sys
    from pathlib import Path as _Path

    monkeypatch.setenv("ZAI_CODING_CN_API_KEY", "test-key")
    flaky = str(_Path(__file__).resolve().parent / "fixtures" / "mcp_flaky_server.py")
    config = _mcp_config(tmp_path, flaky, {"MCP_READY_FILE": str(tmp_path / "never")})
    _patch_agent(monkeypatch, fake_stream("ok"), {})

    assert cli_main.main(["-p", "hi", "--mcp-file", str(config)]) == 0
    assert "mcp flaky" in capsys.readouterr().err
    assert _sys.version  # silence unused-import style complaints in some linters


def test_no_mcp_flag_never_constructs_a_manager(monkeypatch):
    monkeypatch.setenv("ZAI_CODING_CN_API_KEY", "test-key")

    def explode(*args, **kwargs):
        raise AssertionError("McpManager must not be constructed with --no-mcp")

    monkeypatch.setattr(cli_main, "McpManager", explode)
    _patch_agent(monkeypatch, fake_stream("ok"), {})
    assert cli_main.main(["-p", "hi", "--no-mcp"]) == 0


def test_mcp_server_filter_selects_only_named_servers(monkeypatch, tmp_path):
    import json

    monkeypatch.setenv("ZAI_CODING_CN_API_KEY", "test-key")
    config = tmp_path / "mcp.json"
    config.write_text(
        json.dumps(
            {
                "servers": {
                    "alpha": {"command": ["echo", "a"]},
                    "beta": {"command": ["echo", "b"]},
                }
            }
        )
    )
    captured: dict = {}

    class FakeManager:
        def __init__(self, configs, **kwargs):
            captured["configs"] = list(configs)

        async def start(self):
            return []

        def tool_defs(self):
            return []

        async def close(self):
            return None

    monkeypatch.setattr(cli_main, "McpManager", FakeManager)
    _patch_agent(monkeypatch, fake_stream("ok"), {})

    assert cli_main.main(["-p", "hi", "--mcp-file", str(config),
                          "--mcp-server", "beta"]) == 0
    assert captured["configs"] == ["beta"]


# ---------------------------------------------------------------------------
# S2-T4: skills
# ---------------------------------------------------------------------------


def test_skills_are_injected_into_tools_and_prompt(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    skill_dir = tmp_path / ".zenagent" / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo\ndescription: demo skill for tests\n---\nBody here.\n"
    )
    monkeypatch.setenv("ZAI_CODING_CN_API_KEY", "test-key")
    captured: dict = {}

    def build(model, api_key, thinking, gate, stream_fn=None, tools=None,
              session_id=None, transform_context=None, max_turns=None,
              max_cost=None, retries=0, context_files=True, extra_prompt=""):
        captured["tools"] = [tool.name for tool in (tools or [])]
        captured["extra"] = extra_prompt
        return Agent(model=model, system_prompt="s", api_key=api_key, stream_fn=fake_stream())

    monkeypatch.setattr(cli_main, "_build_agent", build)
    assert cli_main.main(["-p", "hi"]) == 0
    assert "read_skill" in captured["tools"]
    assert "demo" in captured["extra"]


def test_no_skills_flag_disables_discovery(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    skill_dir = tmp_path / ".zenagent" / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: demo\ndescription: d\n---\n")
    monkeypatch.setenv("ZAI_CODING_CN_API_KEY", "test-key")
    captured: dict = {}

    def build(model, api_key, thinking, gate, stream_fn=None, tools=None,
              session_id=None, transform_context=None, max_turns=None,
              max_cost=None, retries=0, context_files=True, extra_prompt=""):
        captured["tools"] = [tool.name for tool in (tools or [])]
        captured["extra"] = extra_prompt
        return Agent(model=model, system_prompt="s", api_key=api_key, stream_fn=fake_stream())

    monkeypatch.setattr(cli_main, "_build_agent", build)
    assert cli_main.main(["-p", "hi", "--no-skills"]) == 0
    assert "read_skill" not in captured["tools"]
    assert captured["extra"] == ""


def test_custom_provider_from_config_is_used(monkeypatch, tmp_path):
    from zenagent.config import UserConfig
    from zenagent.providers.catalog import ProviderConfig, model_from_mapping

    model = model_from_mapping(
        "local",
        "http://127.0.0.1:8080/v1",
        "openai-completions",
        {"id": "my-model", "context_window": 1000, "max_tokens": 100, "reasoning": False},
    )
    provider = ProviderConfig(
        id="local",
        name="Local",
        base_url=model.base_url,
        api_key_env="LOCAL_API_KEY",
        adapter="openai-completions",
        models=[model],
    )
    monkeypatch.setenv("LOCAL_API_KEY", "test-key")
    monkeypatch.setattr(
        cli_main,
        "load_user_config",
        lambda *a, **k: UserConfig(default_model="my-model", custom_providers={"local": provider}),
    )
    captured: dict = {}

    def build(model, api_key, thinking, gate, stream_fn=None, tools=None,
              session_id=None, transform_context=None, max_turns=None,
              max_cost=None, retries=0, context_files=True, extra_prompt=""):
        captured["model"] = model
        return Agent(model=model, system_prompt="s", api_key=api_key, stream_fn=fake_stream())

    monkeypatch.setattr(cli_main, "_build_agent", build)
    assert cli_main.main(["-p", "hi"]) == 0
    assert captured["model"].id == "my-model"
    assert captured["model"].provider == "local"


def test_policy_flags_reach_the_permission_gate(monkeypatch):
    from zenagent.config import UserConfig

    monkeypatch.setenv("ZAI_CODING_CN_API_KEY", "test-key")
    monkeypatch.setattr(
        cli_main,
        "load_user_config",
        lambda: UserConfig(tool_allow=["read_*"], tool_deny=["bash"]),
    )
    captured: dict = {}

    def build(model, api_key, thinking, gate, stream_fn=None, tools=None,
              session_id=None, transform_context=None, max_turns=None,
              max_cost=None, retries=0, context_files=True, extra_prompt=""):
        captured["gate"] = gate
        return Agent(model=model, system_prompt="s", api_key=api_key, stream_fn=fake_stream())

    monkeypatch.setattr(cli_main, "_build_agent", build)
    assert cli_main.main(["-p", "hi", "--read-only"]) == 0
    gate = captured["gate"]
    assert gate.read_only is True
    assert gate.allow == ["read_*"]
    assert gate.deny == ["bash"]


def test_theme_flag_is_forwarded_to_the_repl(monkeypatch):
    import zenagent.cli.repl as repl_module

    captured: dict = {}

    async def fake_repl(**kwargs):
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(repl_module, "repl", fake_repl)
    assert cli_main.main(["--theme", "mono"]) == 0
    assert captured["theme"] == "mono"


def test_version_flag_prints_the_version(capsys):
    import pytest

    from zenagent import __version__
    from zenagent.cli.main import build_parser

    parser = build_parser()
    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(["--version"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out + capsys.readouterr().err
    assert __version__ in out
