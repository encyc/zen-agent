"""The one JSONL discipline: fsync every append, repair torn tails before writing.

Append-only JSONL is the durability contract for session transcripts, the run
ledger and swarm rooms. Two things make it survive a crash mid-write:

* every append is flushed **and fsynced** before ``append_line`` returns, so a
  power cut loses at most the line being written;
* an append whose file does not end in a newline first truncates the torn tail
  (nothing after the last newline is a record). Without this, the next append
  concatenates onto the half line and one broken ``json.loads`` silently drops
  BOTH records on replay.

``room.jsonl`` had this treatment first (deltas v6); this module is it, shared.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

__all__ = ["append_line", "dump_line", "repair_tail"]


def dump_line(payload: dict, **dumps_kwargs) -> str:
    """One record as one line (newline included)."""
    return json.dumps(payload, ensure_ascii=False, **dumps_kwargs) + "\n"


def repair_tail(path: Path) -> None:
    """Drop everything after the final newline (a torn record, not data).

    Complete files, empty files and absent files are left untouched. The
    truncation is fsynced so a crash right after it cannot resurrect the tail.
    """
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return
    if not data or data.endswith(b"\n"):
        return
    cut = data.rfind(b"\n") + 1
    with path.open("r+b") as handle:
        handle.truncate(cut)
        handle.flush()
        os.fsync(handle.fileno())


def append_line(path: Path, line: str) -> None:
    """Append one already-serialized line durably.

    Repairs a torn tail first, so a crash mid-append can never corrupt the
    record that follows it. OSError propagates: callers decide whether a
    failed write is fatal or best-effort (they own that policy).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    repair_tail(path)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line if line.endswith("\n") else line + "\n")
        handle.flush()
        os.fsync(handle.fileno())
