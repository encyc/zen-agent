"""Skills: markdown playbooks the model loads on demand.

A skill is a directory with a ``SKILL.md``:

    ---
    name: pdf-report
    description: Build a PDF status report from a CSV
    ---
    (instructions the model reads when it decides the skill applies)

Only the name + description go into the system prompt; the body is fetched with
the ``read_skill`` tool, so a large library of skills costs almost no context.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from zenagent.agent.loop import AgentTool, ToolResultAgent
from zenagent.frontmatter import parse_frontmatter
from zenagent.types import TextContent

__all__ = [
    "MAX_INDEX_BYTES",
    "MAX_SKILL_BODY_BYTES",
    "Skill",
    "build_skill_tool",
    "default_skill_roots",
    "discover_skills",
    "skills_prompt_index",
]

#: The file that marks a directory as a skill.
SKILL_FILE = "SKILL.md"

#: Cap for the skill index injected into the system prompt.
MAX_INDEX_BYTES = 4096

#: Cap for a skill body returned by ``read_skill``.
MAX_SKILL_BODY_BYTES = 32768

#: Cap for a derived description line.
_MAX_DESCRIPTION = 120


@dataclass
class Skill:
    """One discovered skill."""

    name: str
    description: str
    path: Path
    body: str


def _first_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped[:_MAX_DESCRIPTION]
    return ""


def discover_skills(roots: list[Path]) -> tuple[list[Skill], list[str]]:
    """Find ``<root>/<dir>/SKILL.md`` skills; returns ``(skills, warnings)``.

    Roots are searched in order and the first definition of a name wins, so put
    the project root before the user root to let a project override a global
    skill.
    """
    skills: dict[str, Skill] = {}
    warnings: list[str] = []
    for root in roots:
        directory_root = Path(root).expanduser()
        if not directory_root.is_dir():
            continue
        for directory in sorted(path for path in directory_root.iterdir() if path.is_dir()):
            path = directory / SKILL_FILE
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                warnings.append(f"{path}: cannot read ({exc})")
                continue
            meta, body, problem = parse_frontmatter(text)
            if problem is not None:
                warnings.append(f"{path}: {problem}")
                continue
            name = meta.get("name") or directory.name
            description = meta.get("description") or _first_line(body) or "(no description)"
            if name in skills:
                warnings.append(
                    f"{path}: duplicate skill {name!r} (keeping {skills[name].path})"
                )
                continue
            skills[name] = Skill(
                name=name, description=description, path=path, body=body
            )
    return list(skills.values()), warnings


def build_skill_tool(skills: list[Skill]) -> AgentTool:
    """The ``read_skill`` tool backed by the discovered skills."""
    available = {skill.name: skill for skill in skills}
    names = ", ".join(sorted(available))

    async def execute(call_id, args, abort, on_update=None) -> ToolResultAgent:
        name = str(args.get("name", ""))
        skill = available.get(name)
        if skill is None:
            return ToolResultAgent(
                content=[
                    TextContent(
                        text=f"unknown skill {name!r}; available: {names or '(none)'}"
                    )
                ],
                is_error=True,
            )
        body = skill.body
        encoded = body.encode("utf-8")
        if len(encoded) > MAX_SKILL_BODY_BYTES:
            body = (
                encoded[:MAX_SKILL_BODY_BYTES].decode("utf-8", "replace")
                + "\n[... skill truncated ...]"
            )
        return ToolResultAgent(
            content=[TextContent(text=body)],
            details={"skill": skill.name, "path": str(skill.path)},
        )

    return AgentTool(
        name="read_skill",
        description=(
            "Load a skill's instructions by name before following it. "
            f"Available skills: {names or '(none)'}"
        ),
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Skill name to load."}
            },
            "required": ["name"],
        },
        execute=execute,
    )


def skills_prompt_index(skills: list[Skill], max_bytes: int = MAX_INDEX_BYTES) -> str:
    """A bounded index of available skills for the system prompt."""
    if not skills:
        return ""
    lines = [
        "## Skills",
        "Load a skill with the read_skill tool before following its instructions:",
        "",
    ]
    for skill in skills:
        entry = f"- {skill.name}: {skill.description}"
        if len("\n".join([*lines, entry]).encode("utf-8")) > max_bytes:
            lines.append("- ... more skills omitted")
            break
        lines.append(entry)
    return "\n".join(lines)


def default_skill_roots(cwd: Path) -> list[Path]:
    """Project skills first (they override global ones), then the user's.

    The project directory is the nearest ancestor with a ``.git`` marker, so
    opening the agent in a subdirectory still finds the repository's skills.
    """
    from zenagent.project import find_project_root

    root = find_project_root(cwd)
    return [root / ".zenagent" / "skills", Path.home() / ".zenagent" / "skills"]
