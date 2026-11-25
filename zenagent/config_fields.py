"""Value checks shared by the config parser and the section parsers.

These live outside :mod:`zenagent.config` so a section parser
can validate its own table without importing
the config module that imports it. Same error contract throughout: a bad value
is a ``ValueError`` naming the file and ``section.key``, never a silent clamp.
"""

from __future__ import annotations

from pathlib import Path

from zenagent.config_errors import UnknownConfigField

__all__ = [
    "non_negative_float",
    "positive_float",
    "positive_int",
    "unknown_section_keys",
]


def unknown_section_keys(
    table: dict, allowed: frozenset[str], section: str, target: Path
) -> None:
    unknown = set(table) - allowed
    if unknown:
        raise UnknownConfigField.keys(
            target=target, prefix=f"[{section}]", unknown=unknown, allowed=allowed
        )


def positive_int(table: dict, key: str, default: int, section: str, target: Path) -> int:
    value = table.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"config {target}: {section}.{key} must be a positive integer")
    return value


def positive_float(
    table: dict, key: str, default: float, section: str, target: Path
) -> float:
    value = table.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f"config {target}: {section}.{key} must be a positive number")
    return float(value)


def non_negative_float(
    table: dict, key: str, default: float, section: str, target: Path
) -> float:
    value = table.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ValueError(
            f"config {target}: {section}.{key} must be a non-negative number"
        )
    return float(value)
