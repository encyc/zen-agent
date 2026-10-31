"""Prompt subsystem: block files, rendering and composition.

Content lives in Markdown block files (``zenagent/prompts/blocks/``); metadata
lives in :mod:`zenagent.prompts.spec`. ``compose_system`` is the single
assembly contract every frontend will use; for now it covers the base system
blocks, with context/skills/extensions/mode added by the next task.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from zenagent.prompts.registry import read_block
from zenagent.prompts.render import PromptRenderError, render, template_vars
from zenagent.prompts.spec import BLOCKS, PROMPT_VERSION, SYSTEM_BLOCK_ORDER

__all__ = [
    "BLOCKS",
    "PROMPT_VERSION",
    "PromptContext",
    "PromptRenderError",
    "RenderedBlock",
    "RenderedPrompt",
    "compose_system",
    "render",
    "template_vars",
]


@dataclass(frozen=True)
class RenderedBlock:
    """One block that contributed to a rendered prompt."""

    id: str
    chars: int
    cache_class: str
    version: str


@dataclass(frozen=True)
class RenderedPrompt:
    """The rendered text plus the provenance of every block that built it."""

    text: str
    blocks: tuple[RenderedBlock, ...]
    version: str


@dataclass(frozen=True)
class PromptContext:
    """Everything the prompt needs from the caller.

    Conditions are evaluated here, not in the templates: the caller decides
    which optional parts exist (``include_*``) and supplies pre-formatted data
    (context text, skill entries, extension fragments).
    """

    cwd: Path
    tool_names: Sequence[str]
    mode: str = "ask"
    context_text: str = ""
    skill_entries: str = ""
    extension_fragments: Sequence[str] = ()
    include_context: bool = True
    include_skills: bool = True
    include_extensions: bool = True
    include_mode: bool = True
    system_override: str | None = None
    root: Path | None = None


def _tool_list(tool_names: Sequence[str]) -> str:
    return ", ".join(tool_names) if tool_names else "(none available yet)"


def _render_system(ctx: PromptContext) -> tuple[str, list[RenderedBlock]]:
    parts: list[str] = []
    blocks: list[RenderedBlock] = []
    tool_list = _tool_list(ctx.tool_names)
    for block_id in SYSTEM_BLOCK_ORDER:
        if block_id == "system.tool_policy_todo" and "todo" not in ctx.tool_names:
            continue
        spec = BLOCKS[block_id]
        values: dict[str, str] = {}
        if "cwd" in spec.required_vars:
            values["cwd"] = str(ctx.cwd)
        if "tool_list" in spec.required_vars:
            values["tool_list"] = tool_list
        text = render(read_block(block_id, ctx.root), values)
        parts.append(text)
        blocks.append(
            RenderedBlock(
                id=block_id,
                chars=len(text),
                cache_class=spec.cache_class,
                version=spec.version,
            )
        )
    return "\n".join(parts), blocks


def compose_system(ctx: PromptContext) -> RenderedPrompt:
    """Render the system prompt for ``ctx``.

    P0 stage: the base system blocks only. Context, skills, extensions and the
    plan note are appended by Task 4.
    """
    if ctx.system_override is not None:
        return RenderedPrompt(
            text=ctx.system_override,
            blocks=(
                RenderedBlock(
                    id="override",
                    chars=len(ctx.system_override),
                    cache_class="stable",
                    version=PROMPT_VERSION,
                ),
            ),
            version=PROMPT_VERSION,
        )
    text, blocks = _render_system(ctx)
    return RenderedPrompt(text=text, blocks=tuple(blocks), version=PROMPT_VERSION)
