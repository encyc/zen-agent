"""Frontend parity: each frontend's context reproduces its golden prompt."""

from __future__ import annotations

from pathlib import Path

import pytest
from prompt_fixtures import CONFIGS, CWD

from zenagent.prompts import PromptContext, compose_system
from zenagent.skills import format_skill_entries

GOLDEN = Path(__file__).resolve().parent / "golden" / "prompts"
FRONTEND_CONFIGS = ("tui-14", "headless-14", "rpc-13", "eval-11")


def frontend_prompt(name: str) -> str:
    cfg = CONFIGS[name]
    return compose_system(
        PromptContext(
            cwd=CWD,
            tool_names=cfg.tool_names,
            mode=cfg.mode,
            context_text=cfg.context_text,
            skill_entries=format_skill_entries(list(cfg.skills)),
            extension_fragments=cfg.extension_fragments,
            system_override=cfg.system_override,
        )
    ).text


@pytest.mark.parametrize("name", FRONTEND_CONFIGS)
def test_frontend_prompt_matches_golden(name: str) -> None:
    expected = (GOLDEN / f"{name}.txt").read_text(encoding="utf-8")
    assert frontend_prompt(name) == expected


def test_fixture_rosters_differ_by_frontend() -> None:
    """The v0.4.1 golden corpus records the per-front roster order, headless
    included. The live contract (``-p`` shares the Runtime roster) is pinned by
    ``tests/test_headless_parity.py``, not here.
    """
    tui = frontend_prompt("tui-14")
    headless = frontend_prompt("headless-14")
    assert "todo, task, read_skill" in tui
    assert "read_skill, todo, task" in headless


async def test_real_runtime_appends_extension_fragments_last(runtime_factory, tmp_path):
    """A real assembly (not a fixture) puts the extension fragment at the end."""
    extension = tmp_path / "fragment_ext.py"
    extension.write_text(
        '''
from zenagent.agent.loop import AgentTool, ToolResultAgent
from zenagent.types import TextContent


async def execute(call_id, args, abort, on_update=None):
    return ToolResultAgent(content=[TextContent(text="ok")])


def register(api):
    api.register_tool(AgentTool(
        name="read_file",
        description="shadow the built-in name",
        parameters={"type": "object"},
        execute=execute,
    ))
    api.add_system_prompt("EXT")
''',
        encoding="utf-8",
    )

    runtime = await runtime_factory(
        tools=[], extension_paths=[str(extension)], context_files=False
    )

    prompt = runtime.agent.system_prompt
    assert prompt.endswith("\n\nEXT")
    assert "Available tools: read_file" in prompt


async def test_repl_prompt_comes_from_the_runtime(runtime_factory) -> None:
    from zenagent.tools.fs import READ_FILE

    runtime = await runtime_factory(tools=[READ_FILE])

    prompt = runtime.agent.system_prompt
    assert "Available tools: read_file" in prompt
    assert "todo" not in prompt
