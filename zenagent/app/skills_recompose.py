"""Skill/template/extension discovery and the tool-list rebuild.

The runtime half of reload (audit P1-9, Task 31): every function here takes
the :class:`~zenagent.app.runtime.Runtime` as its first argument and mutates
it, so the class keeps its callers' surface (``Runtime._recompose`` etc. are
one-line delegates) while this module owns the rebuild logic. The assembly
itself (bootstrap), plugin refresh and the limits knobs are separate slices.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from zenagent import settings
from zenagent.agent.memory_store import MemoryStore
from zenagent.app.rewind import build_checkpoint_tools
from zenagent.extensions import load_extensions
from zenagent.i18n import t
from zenagent.skills import (
    build_skill_tool,
    default_skill_roots,
    discover_skills,
    format_skill_entries,
)
from zenagent.templates import default_template_roots, discover_templates
from zenagent.tools import base_tools
from zenagent.tools.github import build_github_tool
from zenagent.tools.lsp import build_lsp_tool
from zenagent.tools.memory import build_memory_tool, build_memory_write_tool

if TYPE_CHECKING:
    from zenagent.app.runtime import Runtime

__all__ = [
    "active_skills",
    "change_note",
    "recompose",
    "reconcile",
    "reconcile_async",
    "rediscover",
    "refresh_skills",
    "signature",
]


def active_skills(skills: list) -> list:
    """Drop skills the user switched off in settings before they reach the agent."""
    disabled = settings.disabled_skills()
    return [skill for skill in skills if skill.name not in disabled]


def signature(items: list) -> list[tuple[str, str, str, str]]:
    """What makes a discovered item "the same item": a body edit counts.

    Shared by skills and templates; mtimes deliberately do not participate,
    so a ``touch`` does not report a change.
    """
    return [
        (item.name, item.description, str(item.path), item.body) for item in items
    ]


def change_note(label: str, before: list, after: list) -> str:
    old = {item.name: item for item in before}
    new = {item.name: item for item in after}
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    edited = sorted(
        name
        for name in set(old) & set(new)
        if signature([old[name]]) != signature([new[name]])
    )
    note = f"{label} {len(before)} -> {len(after)}"
    if added:
        note += f" (+{', '.join(added)})"
    if removed:
        note += f" (-{', '.join(removed)})"
    if edited:
        note += f" (~{', '.join(edited)})"
    return note


def recompose(runtime: Runtime) -> None:
    """Rebuild the tool list and prompt from the current discovery results.

    Skills, extension tools and MCP tools all contribute to both. The
    desired set goes through :attr:`tool_registry`, which applies
    ``hidden``/``default_inactive`` and any activation override, and reports
    whether the model-visible set changed -- so a reload that finds nothing
    new does not touch the live agent. Rebuilding from scratch (rather than
    patching the previous list) is what keeps ``read_skill``'s roster, the
    prompt's skill index and ``agent.tools`` from drifting apart.
    """
    if runtime._custom_tools is not None:
        tools = [*runtime._custom_tools, *runtime._extension_tools]
        runtime._skill_entries = ""
        runtime._base_tools = tools
        runtime._base_prompt = runtime._build_prompt(tools)
        if hasattr(runtime, "agent"):
            runtime.agent.tools = list(tools)
            runtime.agent.system_prompt = runtime._prompt_with_mode()
        return

    tools = base_tools(
        runtime.ask_tool,
        shell_max_output_bytes=runtime.limits.shell_max_output_bytes,
        cwd=runtime.cwd,
    )
    tools.extend(
        build_checkpoint_tools(lambda: getattr(runtime, "agent", None), runtime.rewind)
    )
    github_tool = build_github_tool()
    if github_tool is not None:
        tools.append(github_tool)
    tools.append(build_lsp_tool())
    # Project memory: always registered; the tools operate on
    # <root>/.zenagent/memory/ and an empty library is not an error.
    runtime.memory_store = MemoryStore(runtime.root)
    tools.append(build_memory_tool(runtime.memory_store))
    tools.append(build_memory_write_tool(runtime.memory_store))
    if runtime.todo_tool is not None:
        tools.append(runtime.todo_tool)
    if runtime.task_tool is not None:
        tools.append(runtime.task_tool)
    for provider in runtime.tool_providers:
        tools.extend(provider(runtime))
    if runtime.mcp is not None:
        tools.extend(runtime.mcp.tool_defs())
    if runtime.skills:
        tools.append(
            build_skill_tool(
                runtime.skills, max_body_bytes=runtime.limits.max_body_bytes
            )
        )
    tools.extend(runtime._extension_tools)
    if runtime.profile.tools_allow is not None:
        tools = [tool for tool in tools if tool.name in runtime.profile.tools_allow]
    tools = [*tools, *runtime.profile.extra_tools]
    runtime._skill_entries = format_skill_entries(
        runtime.skills, max_bytes=runtime.limits.max_index_bytes
    )
    runtime.tool_registry.reconcile(tools)
    active = runtime.tool_registry.active()
    runtime._base_tools = list(active)
    runtime._base_prompt = runtime._build_prompt(active)
    if hasattr(runtime, "agent"):
        runtime.agent.tools = list(active)
        runtime.agent.system_prompt = runtime._prompt_with_mode()


def refresh_skills(runtime: Runtime) -> list[str]:
    """Rescan the skill roots and adopt the result when it changed.

    Cheap and side-effect free -- a directory listing plus the ``SKILL.md``
    reads, well under a millisecond for a normal library -- so a display
    command may call it on every invocation. Unlike :func:`rediscover` it
    never runs extension code.

    Returns change notes (empty when nothing changed). While a run is
    streaming the agent is left untouched: the model has already been told
    which skills exist, and swapping the tool under it would make
    ``read_skill`` disagree with the prompt.
    """
    if not runtime.skills_enabled:
        return []
    skills, warnings = discover_skills(default_skill_roots(runtime.root))
    skills = active_skills(skills)
    for warning in warnings:
        runtime._note(f"skills {warning}")
    if signature(skills) == signature(runtime.skills):
        return []
    note = change_note("skills", runtime.skills, skills)
    if hasattr(runtime, "agent") and runtime.agent.is_streaming:
        return [f"{note}; applies after this run"]
    runtime.skills = skills
    if hasattr(runtime, "agent"):
        recompose(runtime)
    return [note]


def rediscover(runtime: Runtime) -> list[str]:
    """Re-run every discovery step and swap the result into the live agent.

    Skills and templates are file reads, but extensions are *executed*
    again (``importlib`` + ``register(api)``), which is why this belongs on
    an explicit ``/reload`` and never inside a display command.

    Refuses while a run is streaming: tools cannot be swapped under an
    in-flight request. Raises ``RuntimeError``; callers check
    ``agent.is_streaming`` first to render a nicer message.
    """
    if hasattr(runtime, "agent") and runtime.agent.is_streaming:
        raise RuntimeError(t("cmdmsg.cannot_reload"))
    notes: list[str] = []

    if runtime.profile.skills:
        skills, warnings = discover_skills(
            [*default_skill_roots(runtime.root), *runtime._plugin_skill_roots]
        )
        skills = active_skills(skills)
        for warning in warnings:
            runtime._note(f"skills {warning}")
        if signature(skills) != signature(runtime.skills):
            notes.append(change_note("skills", runtime.skills, skills))
        runtime.skills = skills

    if runtime.profile.templates:
        templates, warnings = discover_templates(default_template_roots(runtime.root))
        for warning in warnings:
            runtime._note(f"templates {warning}")
        if signature(templates) != signature(runtime.templates):
            notes.append(change_note("templates", runtime.templates, templates))
        runtime.templates = templates

    if runtime.extension_files:
        loaded = load_extensions(runtime.extension_files)
        for warning in loaded.warnings:
            runtime._note(f"extension {warning}")
        runtime._extension_tools = list(loaded.tools)
        runtime.extension_commands = dict(loaded.commands)
        runtime._extension_prompt = loaded.prompt_text
        runtime.hooks = loaded.hooks
        notes.append(
            f"{len(runtime._extension_tools)} extension tools "
            f"from {len(runtime.extension_files)} files"
        )

    recompose(runtime)
    if hasattr(runtime, "agent"):
        runtime._subscribe_hooks()
    return notes or ["nothing changed"]


def reconcile(runtime: Runtime) -> list[str]:
    """Single entry point for every tool-set change, synchronously.

    MCP refresh is the one async half (see :func:`reconcile_async`); this
    covers skills, extensions and templates, and is what ``/reload`` and
    the discovery tests call. Kept as a thin alias so callers express intent
    ("reconcile the tool set") rather than mechanism ("rediscover").
    """
    return rediscover(runtime)


async def reconcile_async(runtime: Runtime) -> list[str]:
    """The full reload path: reconnect MCP servers, then rediscover files."""
    await runtime.refresh_mcp()
    return rediscover(runtime)
