"""Extensions: loading, isolation, commands, hooks, and CLI wiring."""

import asyncio

from pyagent.agent import Agent
from pyagent.cli.repl import handle_slash
from pyagent.extensions import load_extensions

GOOD = '''
from pyagent.agent.loop import AgentTool, ToolResultAgent
from pyagent.types import TextContent


async def echo(call_id, args, abort, on_update=None):
    return ToolResultAgent(content=[TextContent(text=f"ext:{args.get('text', '')}")])


async def hello(agent, argument):
    return f"hello {argument}"


def register(api):
    api.register_tool(AgentTool(
        name="ext_echo",
        description="echo from an extension",
        parameters={"type": "object", "properties": {"text": {"type": "string"}}},
        execute=echo,
    ))
    api.register_command("hello", "greet someone", hello)
    api.add_system_prompt("EXTENSION PROMPT")
    api.on("agent_end", lambda event: None)
'''


def write(tmp_path, name: str, source: str):
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    return path


def test_loads_tools_commands_prompt_and_hooks(tmp_path):
    loaded = load_extensions([write(tmp_path, "good.py", GOOD)])
    assert loaded.warnings == []
    assert [tool.name for tool in loaded.tools] == ["ext_echo"]
    assert "hello" in loaded.commands
    assert "EXTENSION PROMPT" in loaded.prompt_text
    assert "agent_end" in loaded.hooks


def test_registered_tool_executes(tmp_path):
    loaded = load_extensions([write(tmp_path, "good.py", GOOD)])
    tool = loaded.tools[0]
    result = asyncio.run(tool.execute("c1", {"text": "hi"}, None))
    assert result.content[0].text == "ext:hi"


def test_broken_extensions_are_skipped_with_warnings(tmp_path):
    broken_syntax = write(tmp_path, "syntax.py", "def register(api)\n")
    raises = write(
        tmp_path,
        "raises.py",
        "def register(api):\n    raise RuntimeError('boom')\n",
    )
    no_register = write(tmp_path, "noreg.py", "VALUE = 1\n")
    good = write(tmp_path, "good.py", GOOD)

    loaded = load_extensions([broken_syntax, raises, no_register, good])
    assert len(loaded.warnings) == 3
    assert any("SyntaxError" in warning for warning in loaded.warnings)
    assert any("boom" in warning for warning in loaded.warnings)
    assert any("register" in warning for warning in loaded.warnings)
    assert [tool.name for tool in loaded.tools] == ["ext_echo"]  # good still loaded


def test_duplicate_command_keeps_the_first(tmp_path):
    first = write(tmp_path, "a.py", GOOD)
    second = write(
        tmp_path,
        "b.py",
        "def register(api):\n    api.register_command('hello', 'second', lambda a, b: 'x')\n",
    )
    loaded = load_extensions([first, second])
    assert loaded.commands["hello"].description == "greet someone"
    assert any("already registered" in warning for warning in loaded.warnings)


def test_missing_extension_file_warns(tmp_path):
    loaded = load_extensions([tmp_path / "nope.py"])
    assert loaded.warnings and "not found" in loaded.warnings[0]


async def test_extension_command_runs_via_repl(tmp_path):
    loaded = load_extensions([write(tmp_path, "good.py", GOOD)])
    agent = Agent.__new__(Agent)
    lines: list[str] = []
    await handle_slash(
        agent,
        "hello",
        "bob",
        print_fn=lambda *a, **k: lines.append("".join(str(x) for x in a)),
        extension_commands=loaded.commands,
    )
    assert any("hello bob" in line for line in lines)


def test_config_extensions_are_resolved_relative_to_the_file(tmp_path):
    from pyagent.config import load_user_config

    config = tmp_path / "config.toml"
    config.write_text('extensions = ["ext.py", "/abs/other.py"]\n')
    user_config = load_user_config(config)
    assert user_config.extensions[0] == str((tmp_path / "ext.py").resolve())
    assert user_config.extensions[1] == "/abs/other.py"


def test_cli_loads_extension_tools_prompt_and_hooks(monkeypatch, tmp_path):
    import pyagent.cli.main as cli_main
    from pyagent.config import UserConfig
    from pyagent.events import AssistantEvent
    from pyagent.types import AssistantMessage, Usage

    sentinel = tmp_path / "hook-fired"
    extension = write(
        tmp_path,
        "hook.py",
        f'''
from pyagent.agent.loop import AgentTool
from pyagent.types import TextContent


async def ext_tool(call_id, args, abort, on_update=None):
    return ToolResultAgent(content=[TextContent(text="ok")])


def register(api):
    api.register_tool(AgentTool(name="ext_tool", description="d",
                                parameters={{"type": "object"}}, execute=ext_tool))
    api.add_system_prompt("FROM EXTENSION")
    api.on("agent_end", lambda event: open(r"{sentinel}", "a").write("x"))
''',
    )
    monkeypatch.setenv("ZAI_CODING_CN_API_KEY", "test-key")
    monkeypatch.setattr(cli_main, "load_user_config", lambda *a, **k: UserConfig())

    captured: dict = {}

    def build(model, api_key, thinking, gate, stream_fn=None, tools=None,
              session_id=None, transform_context=None, max_turns=None,
              max_cost=None, retries=0, context_files=True, extra_prompt=""):
        captured["tools"] = [tool.name for tool in (tools or [])]
        captured["extra"] = extra_prompt
        return Agent(model=model, system_prompt="s", api_key=api_key, stream_fn=fake)

    async def fake(model, messages, tools, options):
        partial = AssistantMessage(
            content=[], provider=model.provider, model=model.id,
            usage=Usage(), stop_reason="stop", timestamp=0,
        )
        yield AssistantEvent(type="done", partial=partial, reason="stop")

    monkeypatch.setattr(cli_main, "_build_agent", build)
    assert cli_main.main(["-p", "hi", "--extension", str(extension)]) == 0
    assert "ext_tool" in captured["tools"]
    assert "FROM EXTENSION" in captured["extra"]
    assert sentinel.exists()
