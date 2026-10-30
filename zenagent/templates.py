"""Prompt templates: ``/name args`` expands to a reusable prompt.

A template is a markdown file under ``.zenagent/commands/<name>.md`` (project
first, then the user's home). Optional frontmatter can override the name and
give a description for ``/help`` and completion.

Placeholders: ``{{args}}`` expands to the argument, ``{{args:default}}`` falls
back to the default when no argument is given. A template without placeholders
gets the argument appended as its own paragraph.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from zenagent.frontmatter import parse_frontmatter

__all__ = [
    "PromptTemplate",
    "default_template_roots",
    "discover_templates",
    "expand",
]

_ARGS = re.compile(r"\{\{\s*args(?::([^}]*))?\s*\}\}")


@dataclass
class PromptTemplate:
    """One reusable prompt."""

    name: str
    description: str
    path: Path
    body: str


def expand(body: str, argument: str) -> str:
    """Fill a template with ``argument`` (or append it when there is no slot)."""
    if _ARGS.search(body):
        return _ARGS.sub(
            lambda match: argument if argument else (match.group(1) or ""),
            body,
        )
    if argument:
        return f"{body.rstrip()}\n\n{argument}"
    return body


def discover_templates(roots: list[Path]) -> tuple[list[PromptTemplate], list[str]]:
    """Find ``<root>/<name>.md`` templates; first definition of a name wins."""
    templates: dict[str, PromptTemplate] = {}
    warnings: list[str] = []
    for root in roots:
        directory_root = Path(root).expanduser()
        if not directory_root.is_dir():
            continue
        for path in sorted(directory_root.glob("*.md")):
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
            name = meta.get("name") or path.stem
            description = meta.get("description") or _first_line(body) or name
            if name in templates:
                warnings.append(
                    f"{path}: duplicate template {name!r} (keeping {templates[name].path})"
                )
                continue
            templates[name] = PromptTemplate(
                name=name, description=description, path=path, body=body
            )
    return list(templates.values()), warnings


def _first_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped[:120]
    return ""


def default_template_roots(cwd: Path) -> list[Path]:
    """Project templates first (they override global ones), then the user's.

    Like skills, the project directory is the nearest ``.git`` ancestor, so
    subdirectory launches still see the repository's templates.
    """
    from zenagent.project import find_project_root

    root = find_project_root(cwd)
    return [root / ".zenagent" / "commands", Path.home() / ".zenagent" / "commands"]
