"""Minimal ``---`` frontmatter parser shared by skills and prompt templates.

Only flat ``key: value`` pairs are supported: that is all the ecosystem
convention needs (``name``, ``description``) and it keeps the kernel free of a
YAML dependency.
"""

from __future__ import annotations

__all__ = ["parse_frontmatter"]


def parse_frontmatter(text: str) -> tuple[dict[str, str], str, str | None]:
    """Split ``---`` frontmatter from the body.

    Returns ``(meta, body, problem)``. ``problem`` is set when the block opens
    but never closes, or when a line has no ``:`` — callers decide whether to
    skip the file or tolerate it.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text, None
    end: int | None = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            end = index
            break
    if end is None:
        return {}, text, "frontmatter is not closed with ---"
    meta: dict[str, str] = {}
    for line in lines[1:end]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, separator, value = line.partition(":")
        if not separator or not key.strip():
            return {}, text, f"bad frontmatter line: {stripped!r}"
        meta[key.strip()] = value.strip().strip('"').strip("'")
    return meta, "\n".join(lines[end + 1 :]).lstrip("\n"), None
