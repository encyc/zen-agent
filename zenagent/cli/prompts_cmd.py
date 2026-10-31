"""``zen prompts``: inspect the prompt blocks without running a model.

Read-only and offline: it renders blocks with the current working directory and
the built-in tool roster, so operators can see sizes and budgets before a run.
"""

from __future__ import annotations

import sys
from math import ceil
from pathlib import Path

from zenagent.prompts import (
    BLOCKS,
    PROMPT_VERSION,
    PromptContext,
    compose_system,
    read_block,
)
from zenagent.prompts.spec import TOTAL_BUDGET_CHARS
from zenagent.tools import ALL_TOOLS

__all__ = ["main"]

_USAGE = "usage: zen prompts <list|show <id>|estimate [--plan]>"


def _list() -> int:
    print(f"prompt version: {PROMPT_VERSION}")
    for block_id in sorted(BLOCKS):
        spec = BLOCKS[block_id]
        chars = len(read_block(block_id))
        print(
            f"{block_id:26s} {spec.cache_class:9s} "
            f"{chars:5d}/{spec.budget_chars:<5d} {spec.version}"
        )
    return 0


def _show(block_id: str) -> int:
    try:
        print(read_block(block_id))
    except KeyError:
        print(f"unknown prompt block: {block_id}", file=sys.stderr)
        return 2
    return 0


def _estimate(plan: bool) -> int:
    tool_names = tuple(tool.name for tool in ALL_TOOLS) + ("todo", "task")
    rendered = compose_system(
        PromptContext(
            cwd=Path.cwd(),
            tool_names=tool_names,
            mode="plan" if plan else "ask",
            include_context=False,
            include_skills=False,
            include_extensions=False,
        )
    )
    for block in rendered.blocks:
        print(f"{block.id:26s} {block.chars:5d} {block.cache_class}")
    total = len(rendered.text)
    tokens = ceil(total / 4)
    print(f"total: {total} chars (budget {TOTAL_BUDGET_CHARS}) · ~{tokens} tokens")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print(_USAGE, file=sys.stderr)
        return 2
    command, rest = args[0], args[1:]
    if command == "list":
        return _list()
    if command == "show":
        if not rest:
            print(_USAGE, file=sys.stderr)
            return 2
        return _show(rest[0])
    if command == "estimate":
        return _estimate("--plan" in rest)
    print(_USAGE, file=sys.stderr)
    return 2
