"""Content-addressed spill for large image payloads.

Base64 images can dwarf the rest of a transcript line (a screenshot is
megabytes of base64). This store moves the bytes out of the JSONL: the line
keeps a ``{"blob": "<sha256>"}`` ref and the payload lives in a per-directory
``_blobs/<sha>.b64`` file beside the session file. Loading hydrates the data
back, so the wire view is unchanged; the file stays grep-able and small.

Design:

* **best effort** — every failure path degrades to the previous inline
  behavior rather than breaking the run (the ``ArtifactStore`` philosophy):
  offload falls back to the inline bytes on any ``OSError``, hydrate turns a
  missing file into a warning;
* **content-addressed** — the ref is the SHA-256 of the payload, so the same
  image never duplicates across sessions or rewrites, and a ref without its
  file is detectable by construction;
* **atomic** — the payload is written to a temp file in the blob directory
  and ``os.replace``d into place, so a crashed write never leaves a half
  blob behind a committed ref;
* **scoped** — blobs live beside the session file (``<dir>/_blobs``), keyed
  by content only; deleting the directory degrades images to empty data with
  a load warning, never an exception.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import tempfile
from pathlib import Path

__all__ = ["BLOB_MIN_CHARS", "BlobStore", "blob_root_for"]

#: Image data shorter than this stays inline in the transcript line.
BLOB_MIN_CHARS = 4096

#: A well-formed blob ref: 64 lowercase hex characters. Anything else is
#: skipped on hydration (a malformed ref must never escape the root).
_REF = re.compile(r"[0-9a-f]{64}")

#: Prefix of in-flight temp files.
_TEMP_PREFIX = ".tmp-"


def blob_root_for(session_path: str | Path) -> Path:
    """The blob directory beside ``session_path``: ``<dir>/_blobs``."""
    return Path(session_path).parent / "_blobs"


class BlobStore:
    """A directory of base64 payloads addressed by their SHA-256."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def offload(self, payload: dict) -> dict:
        """Replace large inline image blocks with ``blob`` refs, in place.

        Walks the top-level ``content`` list only (images never nest deeper
        in a message dict). Any ``OSError`` leaves the payload inline: a full
        or unwritable disk must not break the run.
        """
        try:
            if not isinstance(payload, dict):
                return payload
            content = payload.get("content")
            if not isinstance(content, list):
                return payload
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "image":
                    continue
                data = block.get("data")
                if not isinstance(data, str) or len(data) < BLOB_MIN_CHARS:
                    continue
                ref = hashlib.sha256(data.encode("utf-8")).hexdigest()
                if not self._write_blob(ref, data):
                    continue
                block.pop("data", None)
                block["blob"] = ref
            return payload
        except OSError:
            return payload

    def hydrate(self, payload: dict) -> list[str]:
        """Restore ``data`` from ``blob`` refs, in place; return missing refs.

        A ref whose file cannot be read stays a ref: the caller reports it
        and the image degrades to empty data instead of an exception.
        """
        if not isinstance(payload, dict):
            return []
        content = payload.get("content")
        if not isinstance(content, list):
            return []
        missing: list[str] = []
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "image":
                continue
            if block.get("data") or "blob" not in block:
                continue
            ref = block["blob"]
            if not isinstance(ref, str) or not _REF.fullmatch(ref):
                continue
            try:
                block["data"] = (self.root / f"{ref}.b64").read_text(
                    encoding="utf-8"
                )
            except OSError:
                missing.append(ref)
        return missing

    def _write_blob(self, ref: str, data: str) -> bool:
        target = self.root / f"{ref}.b64"
        if target.is_file():
            return True
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.root, prefix=_TEMP_PREFIX)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(data)
                os.replace(tmp, target)
            except BaseException:
                with contextlib.suppress(OSError):
                    os.unlink(tmp)
                raise
            return True
        except OSError:
            return False
