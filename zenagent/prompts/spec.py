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
