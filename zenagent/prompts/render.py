"""Single-pass ``{{name}}`` rendering for prompt block files.

Templates support variable substitution only; control flow lives in Python.
Values are inserted verbatim and never re-scanned, so data containing
``{{...}}`` (an AGENTS.md body, an extension fragment) passes through
unchanged. A missing value is an error, never an empty string.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

__all__ = ["PromptRenderError", "render", "template_vars"]

_VAR = re.compile(r"\{\{([a-z0-9_]+)\}\}")


class PromptRenderError(ValueError):
    """A template referenced a value the caller did not supply."""


def template_vars(template: str) -> set[str]:
    """Return every variable name referenced by ``template``."""
    return set(_VAR.findall(template))


def render(template: str, values: Mapping[str, str]) -> str:
    """Substitute ``{{name}}`` placeholders in one pass.

    Raises :class:`PromptRenderError` when a placeholder has no value.
    """
    parts: list[str] = []
    position = 0
    for match in _VAR.finditer(template):
        name = match.group(1)
        if name not in values:
            raise PromptRenderError(f"missing value for {{{{{name}}}}}")
        parts.append(template[position : match.start()])
        parts.append(str(values[name]))
        position = match.end()
    parts.append(template[position:])
    return "".join(parts)
