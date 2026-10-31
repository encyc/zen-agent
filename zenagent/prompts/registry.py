"""Load prompt block files from the package (or a variant overlay root).

The registry is intentionally dumb: read a file, cache it, validate that the
template's variables match the spec's declaration. Composition lives in
:mod:`zenagent.prompts`.
"""

from __future__ import annotations

from functools import lru_cache
from importlib import resources
from pathlib import Path

from zenagent.prompts.render import template_vars
from zenagent.prompts.spec import BLOCKS

__all__ = ["read_block", "validate_registry"]


@lru_cache(maxsize=None)
def _read_package(block_id: str) -> str:
    spec = BLOCKS[block_id]
    return (
        resources.files("zenagent.prompts")
        .joinpath("blocks", *spec.path.split("/"))
        .read_text(encoding="utf-8")
    )


@lru_cache(maxsize=None)
def _read_overlay(block_id: str, root: str) -> str:
    spec = BLOCKS[block_id]
    return (Path(root) / "blocks" / spec.path).read_text(encoding="utf-8")


def read_block(block_id: str, root: Path | None = None) -> str:
    """Return one block's template text.

    ``root`` points at a variant overlay directory (``<root>/blocks/...``);
    ``None`` reads the package copy.
    """
    if block_id not in BLOCKS:
        raise KeyError(f"unknown prompt block: {block_id}")
    if root is None:
        return _read_package(block_id)
    return _read_overlay(block_id, str(root))


def validate_registry(root: Path | None = None) -> list[str]:
    """Return every problem with the block files; empty means valid."""
    problems: list[str] = []
    for block_id, spec in BLOCKS.items():
        try:
            content = read_block(block_id, root)
        except OSError as exc:
            problems.append(f"{block_id}: cannot read {spec.path}: {exc}")
            continue
        actual = template_vars(content)
        declared = set(spec.required_vars)
        if actual != declared:
            problems.append(
                f"{block_id}: template vars {sorted(actual)} != declared {sorted(declared)}"
            )
        if spec.budget_chars <= 0:
            problems.append(f"{block_id}: budget must be positive")
    return problems
