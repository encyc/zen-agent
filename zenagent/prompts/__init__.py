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
from zenagent.prompts.spec import (
    BLOCKS,
    PROMPT_VERSION,
    SYSTEM_BLOCK_ORDER,
    TOOL_BLOCK_NAMES,
)

__all__ = [
    "BLOCKS",
    "PROMPT_VERSION",
    "TOOL_BLOCK_NAMES",
    "PromptContext",
    "PromptRenderError",
    "RenderedBlock",
    "RenderedPrompt",
    "compose_system",
    "render",
    "template_vars",
    "tool_description",
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


def _block(block_id: str, text: str) -> RenderedBlock:
    spec = BLOCKS[block_id]
    return RenderedBlock(
        id=block_id, chars=len(text), cache_class=spec.cache_class, version=spec.version
    )


def compose_system(ctx: PromptContext) -> RenderedPrompt:
    """Render the system prompt for ``ctx``.

    Assembly order matches v0.4.1: base system blocks, then the project
    context, skills index, extension fragments and (plan mode only) the plan
    note. Appends keep the legacy ``"\\n\\n"`` join exactly — the golden parity
    tests pin this byte for byte.
    """
    blocks: list[RenderedBlock] = []
    if ctx.system_override is not None:
        text = ctx.system_override
        blocks.append(
            RenderedBlock(
                id="override",
                chars=len(text),
                cache_class="stable",
                version=PROMPT_VERSION,
            )
        )
    else:
        text, system_blocks = _render_system(ctx)
        blocks.extend(system_blocks)
    if ctx.include_context and ctx.context_text.strip():
        part = render(
            read_block("context_files", ctx.root), {"context_text": ctx.context_text}
        )
        text = f"{text.rstrip()}\n\n{part}"
        blocks.append(_block("context_files", part))
    if ctx.include_skills and ctx.skill_entries.strip():
        part = render(read_block("skills", ctx.root), {"entries": ctx.skill_entries})
        text = f"{text}\n\n{part}"
        blocks.append(_block("skills", part))
    if ctx.include_extensions and ctx.extension_fragments:
        part = "\n\n".join(ctx.extension_fragments)
        text = f"{text}\n\n{part}"
        blocks.append(
            RenderedBlock(
                id="extensions",
                chars=len(part),
                cache_class="session",
                version=PROMPT_VERSION,
            )
        )
    if ctx.include_mode and ctx.mode == "plan":
        part = read_block("plan_mode", ctx.root)
        text = f"{text}\n\n{part}"
        blocks.append(_block("plan_mode", part))
    return RenderedPrompt(text=text, blocks=tuple(blocks), version=PROMPT_VERSION)


def tool_description(name: str, *, root: Path | None = None, **values: str) -> str:
    """Render one tool's description block (``tool.<name>``).

    Tool modules pass their implementation constants as keyword values, so the
    block holds placeholders and the constants stay the only source of truth.
    """
    return render(read_block(f"tool.{name}", root), values)
