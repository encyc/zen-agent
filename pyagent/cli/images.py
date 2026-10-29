"""Read image attachments for vision-capable models.

MIME is decided by magic bytes, not by the file extension, and everything is
validated locally before a request is built: sending a 20MB "png" that is
actually a text file wastes a round trip and a confusing provider error.
"""

from __future__ import annotations

import base64
from pathlib import Path

from pyagent.types import ImageContent

__all__ = ["MAX_IMAGE_BYTES", "read_image_argument"]

#: Largest attachment accepted (per image).
MAX_IMAGE_BYTES = 5 * 1024 * 1024

#: Magic-byte prefixes → MIME type.
_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"RIFF", "image/webp"),  # RIFF....WEBP, checked below
)


def _sniff_mime(data: bytes) -> str | None:
    for prefix, mime in _MAGIC:
        if data.startswith(prefix):
            if prefix == b"RIFF" and data[8:12] != b"WEBP":
                return None
            return mime
    return None


def read_image_argument(path: Path, max_bytes: int = MAX_IMAGE_BYTES) -> ImageContent:
    """Load one image file as an :class:`ImageContent` block.

    Raises ``ValueError`` for missing files, oversized files, or content that is
    not a supported image format.
    """
    target = Path(path)
    try:
        stat = target.stat()
    except OSError as exc:
        raise ValueError(f"cannot read image {target}: {exc}") from exc
    if not target.is_file():
        raise ValueError(f"not a file: {target}")
    if stat.st_size > max_bytes:
        raise ValueError(
            f"image {target} is {stat.st_size} bytes; the limit is {max_bytes}"
        )

    data = target.read_bytes()
    mime = _sniff_mime(data)
    if mime is None:
        raise ValueError(f"{target} does not look like a png/jpeg/gif/webp image")
    return ImageContent(data=base64.b64encode(data).decode("ascii"), mime_type=mime)
