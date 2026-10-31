"""Terminal-width helpers for TUI text.

Terminals lay out CJK glyphs two cells wide, so ``len()`` is not a row width.
Anything that clips text to one line (sidebar rows, dialog bodies, context
slices) must measure cells instead.
"""

from __future__ import annotations

from rich.cells import cell_len, set_cell_size

__all__ = ["cell_len", "clip_cells", "clip_cells_left", "pad_cells"]


def clip_cells(text: str, width: int, ellipsis: str = "…") -> str:
    """Clip ``text`` to ``width`` terminal cells, adding ``ellipsis`` if cut.

    ``width`` counts cells, so a string of CJK glyphs is clipped roughly twice
    as early as the same number of ASCII characters. An empty/negative width
    yields an empty string; a string that already fits is returned untouched.
    """
    if width <= 0:
        return ""
    if cell_len(text) <= width:
        return text
    keep = max(0, width - cell_len(ellipsis))
    return set_cell_size(text, keep) + ellipsis


def clip_cells_left(text: str, width: int, ellipsis: str = "…") -> str:
    """Clip ``text`` from the left (paths keep their tail), counting cells."""
    if width <= 0:
        return ""
    if cell_len(text) <= width:
        return text
    keep = max(0, width - cell_len(ellipsis))
    tail = ""
    for char in reversed(text):
        if cell_len(char + tail) > keep:
            break
        tail = char + tail
    return ellipsis + tail


def pad_cells(text: str, width: int) -> str:
    """Pad ``text`` with spaces to ``width`` cells (clip when it is too long)."""
    missing = width - cell_len(text)
    if missing <= 0:
        return clip_cells(text, width)
    return text + " " * missing
