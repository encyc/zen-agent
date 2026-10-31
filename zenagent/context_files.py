"""Project instruction files (``AGENTS.md`` and friends).

A coding agent that ignores the repository's own conventions is a bad
collaborator, so the harness looks for instruction files between the working
directory and the repository root and appends them to the system prompt.

Scope is deliberately small: a bounded size, a fixed order, and a switch to
turn the whole mechanism off (``--no-context-files``) for reproducible runs.
"""

from __future__ import annotations

from pathlib import Path

from zenagent.prompts.registry import read_block
from zenagent.prompts.render import render

__all__ = [
    "CONTEXT_FILE_NAMES",
    "MAX_CONTEXT_BYTES",
    "discover_context_files",
    "load_context_files",
    "system_prompt_with_context",
]

#: Per directory, first match wins (mirrors the common ecosystem conventions).
CONTEXT_FILE_NAMES = ("AGENTS.md", "CLAUDE.md", ".zenagent/AGENTS.md")

#: Total bytes of instruction text injected into the prompt.
MAX_CONTEXT_BYTES = 32_768


def discover_context_files(start: Path) -> list[Path]:
    """Find instruction files from the repository root down to ``start``.

    Walking stops *after* the first directory that contains ``.git`` so a
    project cannot accidentally inherit instructions from a parent checkout.
    """
    ancestors: list[Path] = []
    current = Path(start).resolve()
    marker_found = False
    while True:
        ancestors.append(current)
        if (current / ".git").exists():
            marker_found = True
            break
        if current.parent == current:  # filesystem root, no repo marker
            break
        current = current.parent
    if not marker_found:
        # No project boundary: do not inherit instructions from / or $HOME.
        ancestors = [Path(start).resolve()]

    found: list[Path] = []
    for directory in reversed(ancestors):  # root first, cwd last
        for name in CONTEXT_FILE_NAMES:
            candidate = directory / name
            if candidate.is_file():
                found.append(candidate)
                break
    return found


def load_context_files(paths: list[Path], max_bytes: int = MAX_CONTEXT_BYTES) -> str:
    """Render the files as markdown sections, capped at ``max_bytes`` total."""
    sections: list[str] = []
    used = 0
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        remaining = max_bytes - used
        if remaining <= len(f"### {path}\n"):
            sections.append(f"[... context truncated at {max_bytes} bytes ...]")
            break
        body = text
        if len(body) > remaining:
            dropped = len(body) - remaining
            body = body[:remaining] + f"\n[... truncated {dropped} bytes ...]"
        section = f"### {path}\n{body.rstrip()}"
        sections.append(section)
        used += len(section)
    return "\n\n".join(sections)


def system_prompt_with_context(base: str, context_text: str) -> str:
    """Append project instructions to the base system prompt."""
    if not context_text.strip():
        return base
    block = render(read_block("context_files"), {"context_text": context_text})
    return f"{base.rstrip()}\n\n{block}"
