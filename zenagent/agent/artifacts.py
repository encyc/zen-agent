"""Artifact spill for oversized tool results.

The kernel result budget truncates an over-long tool result before it enters
the transcript. That keeps the context small, but the omitted middle is gone
unless the tool can be re-run. This store writes the *complete* text to a
per-session ``artifacts/<session stem>/`` directory and the budget appends the
path to its truncation notice, so the model can recover the full output with
``read_file``.

Design:

* **best effort** — :meth:`ArtifactStore.save` returns ``None`` on any failure
  and never raises: a full/unwritable disk must degrade to the old plain
  truncation, not break the run;
* **atomic** — the payload is written to a temp file in the same directory and
  ``os.replace``d into place, so a crashed write never leaves a half artifact;
* **bounded** — at most :data:`MAX_ARTIFACT_FILES` files and
  :data:`MAX_ARTIFACT_BYTES` bytes per session; the oldest files are deleted
  first. A single payload larger than the whole byte budget is refused (it
  could not be retained anyway);
* **names** — ``<sanitized tool name>-<serial>.txt``; the serial skips names
  already taken, so recreating a store for the same session never clobbers a
  previous artifact.
"""

from __future__ import annotations

import contextlib
import os
import re
import tempfile
from collections.abc import Callable
from pathlib import Path

__all__ = [
    "MAX_ARTIFACT_BYTES",
    "MAX_ARTIFACT_FILES",
    "ArtifactSink",
    "ArtifactStore",
    "build_artifact_sink",
]

#: Files kept per session before the oldest are pruned.
MAX_ARTIFACT_FILES = 64
#: Total artifact bytes kept per session before the oldest are pruned.
MAX_ARTIFACT_BYTES = 8 * 1024 * 1024

#: What the result budget calls to spill a payload. Returns the saved path, or
#: None when spilling failed (the caller then keeps plain truncation).
ArtifactSink = Callable[[str, str], Path | None]

#: Characters that would confuse a filename are folded to ``_``.
_NAME_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
#: Prefix of in-flight temp files; never pruned as if they were artifacts.
_TEMP_PREFIX = ".tmp-"


class ArtifactStore:
    """A session-scoped directory of oversized tool-result payloads."""

    def __init__(
        self,
        session_path: str | Path,
        *,
        max_files: int | None = None,
        max_bytes: int | None = None,
    ) -> None:
        self.session_path = Path(session_path).expanduser().resolve()
        self.directory = (
            self.session_path.parent / "artifacts" / self.session_path.stem
        )
        # Read the module defaults here (not as bound default arguments) so a
        # caller/test can adjust the module constants before constructing.
        self.max_files = MAX_ARTIFACT_FILES if max_files is None else max_files
        self.max_bytes = MAX_ARTIFACT_BYTES if max_bytes is None else max_bytes
        self._serial = 0

    def save(self, tool_name: str, text: str) -> Path | None:
        """Atomically write ``text`` as an artifact; None when that failed."""
        payload = text.encode("utf-8", "replace")
        if len(payload) > self.max_bytes:
            # One file larger than the whole session budget cannot be retained.
            return None
        temp_path: Path | None = None
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            target = self._next_path(tool_name)
            descriptor, raw_temp = tempfile.mkstemp(
                prefix=_TEMP_PREFIX, dir=self.directory
            )
            temp_path = Path(raw_temp)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, target)
            temp_path = None
            self._prune()
            # A pathological cap (e.g. max_files=0) may prune the new file too:
            # then there is no artifact to point the model at.
            return target if target.exists() else None
        except Exception:  # noqa: BLE001 - spilling is best effort, never fatal
            return None
        finally:
            if temp_path is not None:
                with contextlib.suppress(OSError):
                    temp_path.unlink()

    # -- internals ---------------------------------------------------------

    def _next_path(self, tool_name: str) -> Path:
        safe = _NAME_UNSAFE.sub("_", str(tool_name)).strip("._-") or "result"
        while True:
            self._serial += 1
            candidate = self.directory / f"{safe}-{self._serial:04d}.txt"
            if not candidate.exists():
                return candidate

    def _prune(self) -> None:
        """Delete the oldest artifacts until the retention caps hold."""
        try:
            paths = [
                path
                for path in self.directory.iterdir()
                if path.is_file() and not path.name.startswith(_TEMP_PREFIX)
            ]
        except OSError:
            return
        entries: list[tuple[int, str, Path, int]] = []
        total = 0
        for path in paths:
            try:
                stat = path.stat()
            except OSError:
                continue
            entries.append((stat.st_mtime_ns, path.name, path, stat.st_size))
            total += stat.st_size
        entries.sort(key=lambda entry: (entry[0], entry[1]))
        while entries and (
            len(entries) > self.max_files or total > self.max_bytes
        ):
            _, _, path, size = entries.pop(0)
            try:
                path.unlink()
            except OSError:
                continue
            total -= size


def build_artifact_sink(session_path: str | Path) -> ArtifactSink:
    """Return a ``save`` callable bound to ``session_path``'s artifact dir."""
    return ArtifactStore(session_path).save
