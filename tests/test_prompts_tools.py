"""Tool description blocks: coverage, no orphans, and golden parity.

The descriptions are the largest fixed prompt surface; moving them into blocks
must not change a single character the model sees.
"""

from __future__ import annotations

import json
from pathlib import Path

from prompt_fixtures import SKILLS, TOOL_GOLDEN_NAMES

from zenagent.prompts import TOOL_BLOCK_NAMES, tool_description
from zenagent.skills import build_skill_tool
from zenagent.tools import ALL_TOOLS
from zenagent.tools.task import build_task_tool
from zenagent.tools.todo import build_todo_tool

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "golden" / "prompts"
TOOLS_DIR = ROOT / "zenagent" / "prompts" / "blocks" / "tools"


def _golden() -> dict:
    return json.loads((GOLDEN / "tools.json").read_text(encoding="utf-8"))


def test_tool_blocks_cover_every_tool() -> None:
    assert set(TOOL_BLOCK_NAMES) == set(TOOL_GOLDEN_NAMES)
    for name in TOOL_BLOCK_NAMES:
        assert (TOOLS_DIR / f"{name}.md").is_file(), name


def test_no_orphan_tool_blocks() -> None:
    on_disk = {path.stem for path in TOOLS_DIR.glob("*.md")}
    assert on_disk == set(TOOL_BLOCK_NAMES)


def test_tool_descriptions_match_golden() -> None:
    golden = _golden()
    tools = list(ALL_TOOLS)
    tools.append(build_todo_tool(None))
    tools.append(build_task_tool(model=None, base_tools=[]))
    tools.append(build_skill_tool(list(SKILLS)))
    for tool in tools:
        assert tool.description == golden[tool.name]["text"], tool.name


def test_read_file_constants_flow_into_block() -> None:
    from zenagent.tools.fs import DEFAULT_READ_LIMIT, MAX_READ_LIMIT

    rendered = tool_description(
        "read_file",
        default_read_limit=str(DEFAULT_READ_LIMIT),
        max_read_limit=str(MAX_READ_LIMIT),
    )
    assert rendered == _golden()["read_file"]["text"]


def test_read_skill_names_flow_into_block() -> None:
    assert tool_description("read_skill", skill_names="alpha, beta") == _golden()[
        "read_skill"
    ]["text"]
