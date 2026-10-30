"""Status band composition: ordered segments plus width degradation."""

from __future__ import annotations

from zenagent.cli.tui.status.presets import PRESETS, Preset
from zenagent.cli.tui.status.segments import (
    SEGMENT_IDS,
    SEGMENTS,
    SegmentContext,
    render_status_line,
)

__all__ = [
    "PRESETS",
    "SEGMENTS",
    "SEGMENT_IDS",
    "Preset",
    "SegmentContext",
    "render_status_line",
]
