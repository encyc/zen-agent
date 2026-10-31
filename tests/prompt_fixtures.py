"""Deterministic fixtures shared by the prompt golden/parity tests.

Everything here is fixed data: the golden files must not depend on the machine,
the clock, or the user's real skills/context files. ``CWD`` is the literal path
the golden text contains; the capture script normalizes the real working
directory to it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from zenagent.skills import Skill
from zenagent.tools import ALL_TOOLS

#: The literal working directory baked into golden files.
CWD = Path("/repo")

#: The 11 built-in tools, in ALL_TOOLS order (v0.4.1).
ALL_TOOL_NAMES = tuple(tool.name for tool in ALL_TOOLS)

#: A minimal AGENTS.md body; the wrapper adds "## Project instructions".
AGENTS_TEXT = "### /repo/AGENTS.md\n- Use pytest. Do not add dependencies.\n"

#: One extension prompt fragment.
EXTENSION = "EXT-ONE: keep answers short."

#: Two deterministic skills (bodies are never rendered into the index).
SKILLS = (
    Skill(
        name="alpha",
        description="Do alpha things.",
        path=Path("/repo/.zenagent/skills/alpha/SKILL.md"),
        body="ALPHA BODY",
    ),
    Skill(
        name="beta",
        description="Do beta things.",
        path=Path("/repo/.zenagent/skills/beta/SKILL.md"),
        body="BETA BODY",
    ),
)

#: Fixed transcript for the compaction golden.
TRANSCRIPT = "## user\nfix the bug\n\n## assistant\nI will read the file."


@dataclass(frozen=True)
class Config:
    """One prompt rendering configuration."""

    tool_names: tuple[str, ...]
    context_text: str = ""
    skills: tuple[Skill, ...] = ()
    extension_fragments: tuple[str, ...] = ()
    mode: str = "ask"
    system_override: str | None = None


#: Every config the golden matrix covers. ``compact`` is a marker entry: it has
#: no system-prompt rendering and is captured through ``TRANSCRIPT`` instead.
CONFIGS: dict[str, Config] = {
    "default-11": Config(tool_names=ALL_TOOL_NAMES),
    "tui-14": Config(
        tool_names=ALL_TOOL_NAMES + ("todo", "task", "read_skill"),
        context_text=AGENTS_TEXT,
        skills=SKILLS,
        extension_fragments=(EXTENSION,),
    ),
    "headless-14": Config(
        tool_names=ALL_TOOL_NAMES + ("read_skill", "todo", "task"),
        context_text=AGENTS_TEXT,
        skills=SKILLS,
        extension_fragments=(EXTENSION,),
    ),
    "plan": Config(
        tool_names=ALL_TOOL_NAMES + ("todo", "task", "read_skill"),
        context_text=AGENTS_TEXT,
        skills=SKILLS,
        extension_fragments=(EXTENSION,),
        mode="plan",
    ),
    "skills": Config(tool_names=ALL_TOOL_NAMES, skills=SKILLS),
    "context": Config(tool_names=ALL_TOOL_NAMES, context_text=AGENTS_TEXT),
    "extension": Config(
        tool_names=ALL_TOOL_NAMES, extension_fragments=(EXTENSION,)
    ),
    "rpc-13": Config(
        tool_names=ALL_TOOL_NAMES + ("todo", "task"), context_text=AGENTS_TEXT
    ),
    "eval-11": Config(
        tool_names=ALL_TOOL_NAMES,
        context_text=AGENTS_TEXT,
        skills=SKILLS,
        extension_fragments=(EXTENSION,),
        system_override="You are zenagent, a coding agent. Be concise.",
    ),
    "compact": Config(tool_names=()),
}

#: Tool names whose description is captured in ``tools.json``.
TOOL_GOLDEN_NAMES = ALL_TOOL_NAMES + ("todo", "task", "read_skill")
