"""Budget gate: every block and every tool description stays within §6.3."""

from __future__ import annotations

from prompt_fixtures import CONFIGS, CWD

from zenagent.prompts import BLOCKS, PromptContext, compose_system
from zenagent.prompts.registry import read_block
from zenagent.prompts.spec import (
    PROMPT_VERSION,
    TOOL_BUDGET_CHARS,
    TOOL_TOTAL_BUDGET_CHARS,
    TOTAL_BUDGET_CHARS,
)
from zenagent.skills import build_skill_tool, format_skill_entries
from zenagent.tools import ALL_TOOLS
from zenagent.tools.task import build_task_tool
from zenagent.tools.todo import build_todo_tool


def test_template_budgets_hold() -> None:
    for block_id, spec in BLOCKS.items():
        if block_id.startswith("tool."):
            continue
        assert len(read_block(block_id)) <= spec.budget_chars, block_id


def test_tool_budgets_hold() -> None:
    tools = list(ALL_TOOLS)
    tools.append(build_todo_tool(None))
    tools.append(build_task_tool(model=None, base_tools=[]))
    tools.append(build_skill_tool([]))
    total = 0
    for tool in tools:
        assert len(tool.description) <= TOOL_BUDGET_CHARS, tool.name
        total += len(tool.description)
    assert total <= TOOL_TOTAL_BUDGET_CHARS


def test_composed_prompt_stays_within_budgets() -> None:
    cfg = CONFIGS["plan"]
    rendered = compose_system(
        PromptContext(
            cwd=CWD,
            tool_names=cfg.tool_names,
            mode=cfg.mode,
            context_text=cfg.context_text,
            skill_entries=format_skill_entries(list(cfg.skills)),
            extension_fragments=cfg.extension_fragments,
        )
    )
    # Data-driven blocks (skills entries, context text, transcript) are bounded
    # by their own caps, not by the block template budget; the template check
    # above covers them. Here we check the fixed blocks and the total ceiling.
    data_driven = {"skills", "context_files", "compact.summary", "compact.result"}
    for block in rendered.blocks:
        spec = BLOCKS.get(block.id)
        if spec is not None and block.id not in data_driven:
            assert block.chars <= spec.budget_chars, block.id
    assert len(rendered.text) <= TOTAL_BUDGET_CHARS


def test_prompt_version_is_current() -> None:
    assert PROMPT_VERSION
    assert all(spec.version == PROMPT_VERSION for spec in BLOCKS.values())
