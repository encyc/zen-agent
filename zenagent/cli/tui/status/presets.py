"""Segment order presets for the status band.

omp ships six (default / minimal / compact / full / nerd / ascii); three are
enough here, and they differ only in which segments are listed.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["PRESETS", "Preset"]


@dataclass(frozen=True)
class Preset:
    """An ordered left group and an ordered right group."""

    left: tuple[str, ...]
    right: tuple[str, ...]


PRESETS: dict[str, Preset] = {
    # Everything that still fits: brand, model, effort, mode, where we are,
    # branch, then facts on the right.
    "default": Preset(
        left=("pi", "model", "thinking", "mode", "path", "git"),
        right=(
            "tokens",
            "cache_hit",
            "cost",
            "context_pct",
            "time_spent",
            "token_rate",
            "queued",
            "session_name",
        ),
    ),
    "compact": Preset(
        left=("model", "mode", "path"),
        right=("cost", "context_pct"),
    ),
    "minimal": Preset(
        left=("model", "path"),
        right=("context_pct",),
    ),
}
