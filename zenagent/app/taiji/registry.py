"""The taiji worker registry: which workers exist, on disk, atomically.

The registry is runtime state, not history: a host restart reconciles it
against ``git worktree list`` — a running worker becomes ``interrupted`` (its
session and branch survive on disk and can be resumed), and a finished worker
whose worktree was cleaned up leaves the registry (git history remembers it).
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

TERMINAL_STATUSES = frozenset({"ok", "error", "merged", "killed"})


@dataclass
class WorkerRecord:
    name: str
    branch: str
    base: str
    worktree: str
    status: str
    created_at: float
    updated_at: float
    session: str = ""
    summary: str = ""
    attention: str = ""


@dataclass
class TaijiRegistry:
    records: dict[str, WorkerRecord] = field(default_factory=dict)

    def load(self, path: Path) -> None:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        records: dict[str, WorkerRecord] = {}
        for name, raw in data.items():
            if not isinstance(name, str) or not isinstance(raw, dict):
                continue
            try:
                records[name] = WorkerRecord(
                    name=str(raw["name"]),
                    branch=str(raw["branch"]),
                    base=str(raw["base"]),
                    worktree=str(raw["worktree"]),
                    status=str(raw["status"]),
                    created_at=float(raw["created_at"]),
                    updated_at=float(raw["updated_at"]),
                    session=str(raw.get("session", "")),
                    summary=str(raw.get("summary", "")),
                    attention=str(raw.get("attention", "")),
                )
            except (KeyError, TypeError, ValueError):
                continue
        self.records = records

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {name: asdict(record) for name, record in self.records.items()}
        descriptor, temp_name = tempfile.mkstemp(
            dir=path.parent, prefix=".taiji.", suffix=".tmp"
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            os.replace(temp_name, path)
        except BaseException:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise

    def reconcile(self, worktree_paths: list[str]) -> None:
        alive = set(worktree_paths)
        for name, record in list(self.records.items()):
            if record.worktree not in alive:
                if record.status in TERMINAL_STATUSES:
                    del self.records[name]
                else:
                    record.status = "interrupted"
            elif record.status == "running":
                record.status = "interrupted"
