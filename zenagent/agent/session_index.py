"""Listing index: derived session facts cached beside the sessions directory.

A listing used to read every transcript file to count its messages and derive
its title — one full parse per file per palette open. This cache stores those
derived facts in a ``_index.json`` sidecar next to the session files and
revalidates each entry against the file's ``(size, mtime_ns)``: an append
changes both, a rewrite changes at least one, and a cache hit costs one
``stat`` instead of one transcript parse.

The stored ``title`` is only the *derived* brief (what
``_title_from_messages`` produced): rename overrides live in the titles
sidecar and are applied fresh on every read, so ``/rename`` never needs
invalidation and un-renaming recovers the derived brief.

Design:

* **advisory** — every failure path degrades to a plain re-read (the
  ``ArtifactStore`` philosophy): a corrupt or missing sidecar is an empty
  cache, an unwritable directory skips the flush, listings never fail
  because of the cache;
* **merge-on-flush** — a concurrent lister's entries are re-read from disk
  and merged under this process's fresh entries (last writer wins per file,
  revalidated by stat anyway);
* **known limit** — a rewrite that lands in the same nanosecond with the
  same byte size is indistinguishable from no change; accepted, the next
  real change re-reads;
* **read-only mode** — ``cache=False`` (the read-only consumer) records no
  entries and makes :meth:`SessionIndex.flush` a no-op, so the sidecar is
  never written (an existing one is still honoured: a stat-validated hit is
  read-only and yields the same facts). Tools that declare
  ``approval="read"`` must not write to disk, so they take this mode.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from pathlib import Path

from zenagent.agent.session import Session, SessionInfo

__all__ = ["INDEX_FILENAME", "SessionIndex"]

#: The sidecar file, inside a sessions directory. Starts with ``_`` so the
#: ``*.jsonl`` glob of every consumer skips it.
INDEX_FILENAME = "_index.json"

#: Prefix of in-flight temp files; never pruned as if they were sessions.
_TEMP_PREFIX = ".tmp-index-"


class SessionIndex:
    """A per-directory cache of :meth:`Session.read_info` facts."""

    def __init__(self, directory: Path, *, cache: bool = True) -> None:
        self.directory = Path(directory)
        self.cache = cache
        self._entries: dict | None = None  # lazy: read the sidecar once

    def _load(self) -> dict:
        if self._entries is None:
            try:
                parsed = json.loads(
                    (self.directory / INDEX_FILENAME).read_text(encoding="utf-8")
                )
                self._entries = parsed if isinstance(parsed, dict) else {}
            except (OSError, ValueError):
                self._entries = {}
        return self._entries

    def read(self, path: Path, titles: dict[str, str]) -> SessionInfo:
        """Cached ``read_info``; unreadable files raise and stay uncached.

        A hit rebuilds the ``SessionInfo`` from the entry and the file's
        current stat. A miss reads the transcript with no title override (so
        the cached brief is the derived one) and applies the override on the
        returned info only.

        With ``cache=False`` no entry is ever recorded, so a read is a hit
        only against a sidecar this process did not write; either way the
        returned ``SessionInfo`` is identical and nothing new is remembered.
        """
        stat = path.stat()  # OSError propagates: a vanished file is a miss
        entries = self._load()
        entry = entries.get(path.name)
        if (
            entry is not None
            and entry.get("size") == stat.st_size
            and entry.get("mtime_ns") == stat.st_mtime_ns
        ):
            info = SessionInfo(
                path=path,
                mtime=stat.st_mtime,
                messages=entry["messages"],
                title=entry["title"],
                created_at=entry.get("created_at", 0.0),
                model=entry.get("model", ""),
                derived_from=entry.get("derived_from", ""),
                started=entry.get("started", False),
            )
        else:
            info = Session(path).read_info(titles={})
            if self.cache:
                entries[path.name] = {
                    "size": stat.st_size,
                    "mtime_ns": stat.st_mtime_ns,
                    "messages": info.messages,
                    "title": info.title,
                    "created_at": info.created_at,
                    "model": info.model,
                    "derived_from": info.derived_from,
                    "started": info.started,
                }
        override = titles.get(path.name)
        if override:
            info.title = override
        return info

    def flush(self) -> None:
        """Merge this process's entries into the sidecar; never raise.

        A read that loaded the sidecar but recorded nothing (every file
        already cached) still rewrites the merge — harmless, and simpler
        than tracking dirtiness. In ``cache=False`` mode this is a no-op: a
        read-only consumer must not write the sidecar at all.
        """
        if not self.cache or self._entries is None:
            return
        on_disk: dict = {}
        try:
            parsed = json.loads(
                (self.directory / INDEX_FILENAME).read_text(encoding="utf-8")
            )
            if isinstance(parsed, dict):
                on_disk = parsed
        except (OSError, ValueError):
            pass
        on_disk.update(self._entries)
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.directory, prefix=_TEMP_PREFIX)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(on_disk, handle, ensure_ascii=False)
                os.replace(tmp, self.directory / INDEX_FILENAME)
            except BaseException:
                with contextlib.suppress(OSError):
                    os.unlink(tmp)
                raise
        except OSError:
            pass  # advisory cache: a full disk must not break listings
