"""Block metadata for the prompt subsystem.

Content lives in Markdown files under ``zenagent/prompts/blocks/``; this module
is the typed index: stable ids, versions, cache class, hard character budgets
and the variables each template declares. Adding a block means adding a file
*and* an entry here — deliberate, so the registry can be validated.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

__all__ = [
    "BLOCKS",
    "PROMPT_VERSION",
    "SYSTEM_BLOCK_ORDER",
    "TOOL_BLOCK_NAMES",
    "TOOL_BUDGET_CHARS",
    "TOOL_TOTAL_BUDGET_CHARS",
    "TOTAL_BUDGET_CHARS",
    "BlockSpec",
]

#: Bumped whenever block content changes; recorded in eval reports and the
#: prompt CHANGELOG.
PROMPT_VERSION = "2026-10-31.1"

CacheClass = Literal["stable", "session", "volatile"]


@dataclass(frozen=True)
class BlockSpec:
    """One rendered prompt block."""

    id: str
    path: str
    version: str
    cache_class: CacheClass
    budget_chars: int
    required_vars: tuple[str, ...] = ()
    optional: bool = False


BLOCKS: dict[str, BlockSpec] = {
    "system.identity": BlockSpec(
        id="system.identity",
        path="system/identity.md",
        version=PROMPT_VERSION,
        cache_class="stable",
        budget_chars=120,
    ),
    "system.environment": BlockSpec(
        id="system.environment",
        path="system/environment.md",
        version=PROMPT_VERSION,
        cache_class="stable",
        budget_chars=240,
        required_vars=("cwd", "tool_list"),
    ),
    "system.tool_policy": BlockSpec(
        id="system.tool_policy",
        path="system/tool_policy.md",
        version=PROMPT_VERSION,
        cache_class="stable",
        budget_chars=700,
        optional=True,
    ),
    "system.tool_policy_todo": BlockSpec(
        id="system.tool_policy_todo",
        path="system/tool_policy_todo.md",
        version=PROMPT_VERSION,
        cache_class="session",
        budget_chars=220,
        optional=True,
    ),
    "system.tool_policy_parallel": BlockSpec(
        id="system.tool_policy_parallel",
        path="system/tool_policy_parallel.md",
        version=PROMPT_VERSION,
        cache_class="stable",
        budget_chars=120,
        optional=True,
    ),
    "system.closing": BlockSpec(
        id="system.closing",
        path="system/closing.md",
        version=PROMPT_VERSION,
        cache_class="stable",
        budget_chars=100,
        optional=True,
    ),
    "plan_mode": BlockSpec(
        id="plan_mode",
        path="plan_mode.md",
        version=PROMPT_VERSION,
        cache_class="session",
        budget_chars=700,
        optional=True,
    ),
    "context_files": BlockSpec(
        id="context_files",
        path="context_files.md",
        version=PROMPT_VERSION,
        cache_class="volatile",
        budget_chars=160,
        required_vars=("context_text",),
        optional=True,
    ),
    "skills": BlockSpec(
        id="skills",
        path="skills.md",
        version=PROMPT_VERSION,
        cache_class="session",
        budget_chars=120,
        required_vars=("entries",),
        optional=True,
    ),
    "compact.system": BlockSpec(
        id="compact.system",
        path="compact_system.md",
        version=PROMPT_VERSION,
        cache_class="session",
        budget_chars=200,
    ),
    "compact.summary": BlockSpec(
        id="compact.summary",
        path="compact_summary.md",
        version=PROMPT_VERSION,
        cache_class="volatile",
        budget_chars=1600,
        required_vars=("transcript",),
    ),
    "task_child_system": BlockSpec(
        id="task_child_system",
        path="task_child_system.md",
        version=PROMPT_VERSION,
        cache_class="stable",
        budget_chars=600,
    ),
    "compact.result": BlockSpec(
        id="compact.result",
        path="compact_result.md",
        version=PROMPT_VERSION,
        cache_class="volatile",
        budget_chars=40,
        required_vars=("summary",),
    ),
}

#: Assembly order for the base system prompt. Sub-blocks are joined with a
#: single newline (the v0.4.1 text is line-oriented).
SYSTEM_BLOCK_ORDER: tuple[str, ...] = (
    "system.identity",
    "system.environment",
    "system.tool_policy",
    "system.tool_policy_todo",
    "system.tool_policy_parallel",
    "system.closing",
)


#: One description block per tool; :func:`zenagent.prompts.tool_description`
#: renders these. Keep the order in sync with the tool roster tests.
TOOL_BLOCK_NAMES: tuple[str, ...] = (
    "read_file",
    "write_file",
    "edit_file",
    "list_dir",
    "bash",
    "wait",
    "ask",
    "glob",
    "grep",
    "web_search",
    "fetch",
    "todo",
    "task",
    "read_skill",
)

#: Variables a tool block declares; the tool module supplies the values so the
#: implementation constants stay the single source of truth.
_TOOL_VARS: dict[str, tuple[str, ...]] = {
    "read_file": ("default_read_limit", "max_read_limit"),
    "glob": ("max_glob_results",),
    "grep": ("max_grep_files_per_page", "max_grep_hits_per_file"),
    "web_search": ("max_results",),
    "read_skill": ("skill_names",),
}

for _tool_name in TOOL_BLOCK_NAMES:
    BLOCKS[f"tool.{_tool_name}"] = BlockSpec(
        id=f"tool.{_tool_name}",
        path=f"tools/{_tool_name}.md",
        version=PROMPT_VERSION,
        cache_class="stable",
        budget_chars=900,
        required_vars=_TOOL_VARS.get(_tool_name, ()),
    )


#: Hard budgets from the design doc (§6.3); enforced by the budget test.
TOTAL_BUDGET_CHARS = 9000
TOOL_TOTAL_BUDGET_CHARS = 4400
TOOL_BUDGET_CHARS = 900
