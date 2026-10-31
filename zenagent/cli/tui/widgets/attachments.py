"""Attachment chip bar above the status band (omp's ``attachmentChips`` dock).

The REPL has had ``/image`` for a while; the TUI showed nothing until submit.
One chip per pending image, click removes it. Hidden while there is no image.
"""

from __future__ import annotations

from rich.text import Text
from textual.containers import Horizontal
from textual.widgets import Static

from zenagent.cli.tui import palette
from zenagent.types import ImageContent

__all__ = ["AttachmentChips", "Chip"]

_MUTED = palette.MUTED
_DIM = palette.DIM


class Chip(Static):
    """One pending attachment; clicking it drops that image."""

    def __init__(self, mime_type: str, index: int) -> None:
        self.mime_type = mime_type
        self.index = index
        super().__init__(Text(f"[{mime_type}]", style=_MUTED), classes="attachment-chip")

    @property
    def plain(self) -> str:
        return f"[{self.mime_type}]"

    def on_click(self) -> None:
        self.app.remove_attachment(self.index)


class AttachmentChips(Horizontal):
    """A one-row strip of :class:`Chip`; no images means no row."""

    def __init__(self) -> None:
        super().__init__(id="attachment-chips")
        self.display = False

    def set_attachments(self, blocks: list[ImageContent]) -> None:
        if not blocks:
            for child in list(self.children):
                child.remove()
            self.display = False
            return
        self.display = True
        if [getattr(child, "mime_type", "") for child in self.children] == [
            block.mime_type for block in blocks
        ]:
            return  # same chips; avoid rebuilding on every streamed delta
        for child in list(self.children):
            child.remove()
        for index, block in enumerate(blocks):
            self.mount(Chip(block.mime_type, index))
