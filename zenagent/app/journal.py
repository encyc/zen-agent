"""Project-scoped snapshot journal for write-class tools.

Every successful ``write_file`` / ``edit_file`` records the previous file bytes
(content-addressed under ``backups/blobs/``) plus one manifest line under the
current project's runtime directory. ``/restore`` uses that log to put a run's
files back, refusing to overwrite a file the user changed afterwards.

Storage lives in ``~/.zenagent/projects/<key>/backups/`` rather than next to a
session file: ``/undo`` and ``/fork`` create a new session file and ``/compact``
rewrites the live transcript (the JSONL keeps the full history), and the user
is most likely to want a restore right after one of those.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import stat
import tempfile
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

from zenagent import project
from zenagent.agent.loop import ToolResultAgent
from zenagent.types import TextContent

__all__ = [
    "BackupPolicy",
    "RestoreReport",
    "RunInfo",
    "SnapshotJournal",
    "attach_journal",
    "journal_for",
    "load_backup_policy",
]

_MANIFEST_NAME = "manifest.jsonl"
_BLOBS_DIR = "blobs"
_WRITE_TOOLS = frozenset({"write_file", "edit_file"})
_DIR_MODE = 0o700
_FILE_MODE = 0o600
_CHUNK = 1 << 20


@dataclass(frozen=True)
class BackupPolicy:
    """How much to keep, and how large a file may be before capture is skipped."""

    enabled: bool = True
    max_file_bytes: int = 2 * 1024 * 1024
    keep_runs: int = 50


@dataclass(frozen=True)
class RunInfo:
    """One ``agent_start``-to-``agent_end`` run, as seen by ``/restore list``."""

    run: str
    session: str
    started: str
    files: int
    paths: tuple[str, ...]


@dataclass
class RestoreReport:
    """Outcome of one ``restore()``: what was put back and what was left alone."""

    run: RunInfo | None
    restored: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class _Pending:
    """A captured pre-write state, held between the before/after hooks."""

    path: Path
    before: bytes | None
    existed: bool = False
    skipped: str | None = None


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _chmod(path: Path, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def _accepts_tool(hook) -> bool:
    """Whether a before hook accepts the resolved tool as a third argument."""
    try:
        signature = inspect.signature(hook)
    except (TypeError, ValueError):  # C callable: assume the current contract
        return True
    try:
        signature.bind(None, {}, None)
    except TypeError:
        return False
    return True


def load_backup_policy(config) -> BackupPolicy:
    """Build a :class:`BackupPolicy` from a parsed ``UserConfig``."""
    return BackupPolicy(
        enabled=getattr(config, "backup_enabled", True),
        max_file_bytes=getattr(config, "backup_max_file_bytes", 2 * 1024 * 1024),
        keep_runs=getattr(config, "backup_keep_runs", 50),
    )


def journal_for(
    session_path: Path | str | None,
    policy: BackupPolicy | None = None,
    *,
    cwd: Path | str | None = None,
) -> SnapshotJournal | None:
    """Journal for the current project, or ``None`` when backups are disabled."""
    resolved = policy or BackupPolicy()
    if not resolved.enabled:
        return None
    session = Path(session_path).stem if session_path else ""
    return SnapshotJournal(
        project.project_backups_dir(cwd), resolved, session=session
    )


class SnapshotJournal:
    """Capture pre-write bytes, log committed writes, restore or prune by run."""

    def __init__(
        self,
        backups_dir: Path | str,
        policy: BackupPolicy | None = None,
        *,
        session: str = "",
    ) -> None:
        self.backups_dir = Path(backups_dir)
        self.policy = policy or BackupPolicy()
        self.session = session
        self.run_id: str | None = None
        self.turn = 0
        self._pending: dict[str, _Pending] = {}
        self._lock = asyncio.Lock()
        self._seq = 0
        self._ensure_dirs()
        entries = self._read_manifest()
        self._seq = max((int(entry.get("seq") or 0) for entry in entries), default=0)
        self.prune(entries)

    # -- layout ------------------------------------------------------------

    @property
    def manifest_path(self) -> Path:
        return self.backups_dir / _MANIFEST_NAME

    @property
    def blobs_dir(self) -> Path:
        return self.backups_dir / _BLOBS_DIR

    def _ensure_dirs(self) -> None:
        self.backups_dir.mkdir(parents=True, exist_ok=True)
        _chmod(self.backups_dir, _DIR_MODE)
        self.blobs_dir.mkdir(parents=True, exist_ok=True)
        _chmod(self.blobs_dir, _DIR_MODE)

    def _blob_path(self, digest: str) -> Path:
        return self.blobs_dir / digest[:2] / digest

    def _write_blob(self, digest: str, data: bytes) -> None:
        target = self._blob_path(digest)
        if target.is_file():
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        _chmod(target.parent, _DIR_MODE)
        descriptor, temp_name = tempfile.mkstemp(
            dir=target.parent, prefix=".blob.", suffix=".tmp"
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            _chmod(Path(temp_name), _FILE_MODE)
            os.replace(temp_name, target)
        except BaseException:
            Path(temp_name).unlink(missing_ok=True)
            raise

    def _read_blob(self, digest: str) -> bytes | None:
        try:
            data = self._blob_path(digest).read_bytes()
        except OSError:
            return None
        return data if _sha(data) == digest else None

    # -- manifest ----------------------------------------------------------

    def _read_manifest(self) -> list[dict]:
        """Manifest entries in file order; unreadable/corrupt lines are skipped."""
        try:
            lines = self.manifest_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        entries: list[dict] = []
        for line in lines:
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict):
                entries.append(entry)
        return entries

    async def _append(self, entry: dict) -> None:
        async with self._lock:
            self._seq += 1
            entry["seq"] = self._seq
            line = json.dumps(entry, ensure_ascii=False) + "\n"
            descriptor = os.open(
                self.manifest_path,
                os.O_CREAT | os.O_APPEND | os.O_WRONLY,
                _FILE_MODE,
            )
            try:
                os.write(descriptor, line.encode("utf-8"))
            finally:
                os.close(descriptor)
            _chmod(self.manifest_path, _FILE_MODE)

    def _rewrite_manifest(self, entries: list[dict]) -> None:
        descriptor, temp_name = tempfile.mkstemp(
            dir=self.backups_dir, prefix=".manifest.", suffix=".tmp"
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                for entry in entries:
                    handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            _chmod(Path(temp_name), _FILE_MODE)
            os.replace(temp_name, self.manifest_path)
        except BaseException:
            Path(temp_name).unlink(missing_ok=True)
            raise

    # -- agent events / hooks ---------------------------------------------

    def on_event(self, event) -> None:
        """Track run boundaries: one run per user prompt, one turn per request."""
        kind = getattr(event, "type", "")
        if kind == "agent_start":
            self.run_id = uuid.uuid4().hex[:12]
            self.turn = 0
        elif kind == "turn_start":
            self.turn += 1

    @staticmethod
    def _tool_name(tool_call) -> str:
        if isinstance(tool_call, str):
            return tool_call
        name = getattr(tool_call, "name", "")
        return name if isinstance(name, str) else ""

    async def before_tool_call(self, tool_call, args: dict) -> None:
        """Capture the target's current bytes; never blocks and never raises."""
        if self._tool_name(tool_call) not in _WRITE_TOOLS or not isinstance(args, dict):
            return
        raw = args.get("path")
        key = getattr(tool_call, "id", None)
        if not isinstance(raw, str) or not raw or not isinstance(key, str) or not key:
            return
        path = Path(raw).expanduser()
        pending = _Pending(path=path, before=None)
        self._pending[key] = pending
        try:
            pending.existed = path.is_file()
            if pending.existed:
                size = path.stat().st_size
                if size > self.policy.max_file_bytes:
                    pending.skipped = "too_large"
                else:
                    pending.before = path.read_bytes()
        except OSError:
            pending.skipped = "unreadable"

    async def after_tool_call(self, tool_call, result) -> ToolResultAgent | None:
        """Log a successful write; drop the capture on an error; never raises."""
        key = getattr(tool_call, "id", None)
        pending = self._pending.pop(key, None) if isinstance(key, str) else None
        if pending is None or getattr(result, "is_error", False):
            return None
        if self.run_id is None:
            self.run_id = uuid.uuid4().hex[:12]
            self.turn = 0
        try:
            current = pending.path.read_bytes()
        except OSError:
            return self._note(result, pending.path, "unreadable")
        entry = {
            "run": self.run_id,
            "turn": self.turn,
            "session": self.session,
            "tool": self._tool_name(tool_call),
            "path": str(pending.path),
            "kind": "modified" if pending.existed else "created",
            "blob": None,
            "before_bytes": len(pending.before) if pending.before is not None else 0,
            "after_sha256": _sha(current),
            "skipped": pending.skipped,
            "time": _now(),
        }
        if pending.skipped is None and pending.before is not None:
            digest = _sha(pending.before)
            try:
                self._write_blob(digest, pending.before)
            except OSError:
                entry["skipped"] = "write_failed"
            else:
                entry["blob"] = digest
        try:
            await self._append(entry)
        except OSError:
            return self._note(result, pending.path, "journal unavailable")
        if entry["skipped"]:
            return self._note(result, pending.path, entry["skipped"])
        return None

    @staticmethod
    def _note(
        result: ToolResultAgent, path: Path, reason: str
    ) -> ToolResultAgent:
        notice = (
            f"zenagent: backup skipped ({reason}); "
            f"/restore cannot revert {path}"
        )
        return replace(result, content=[*result.content, TextContent(text=notice)])

    # -- runs / restore ----------------------------------------------------

    def runs(self, limit: int = 10) -> list[RunInfo]:
        """Runs with at least one logged write, newest first."""
        grouped: dict[str, list[dict]] = {}
        for entry in self._read_manifest():
            run = entry.get("run")
            if not isinstance(run, str) or not run:
                continue
            grouped.setdefault(run, []).append(entry)
        infos: list[RunInfo] = []
        for run, entries in grouped.items():
            entries.sort(key=lambda entry: int(entry.get("seq") or 0))
            paths: list[str] = []
            for entry in entries:
                path = entry.get("path")
                if isinstance(path, str) and path not in paths:
                    paths.append(path)
            infos.append(
                RunInfo(
                    run=run,
                    session=str(entries[0].get("session") or ""),
                    started=str(entries[0].get("time") or ""),
                    files=len(paths),
                    paths=tuple(paths),
                )
            )
        infos.reverse()
        return infos[:limit]

    def restore(self, run_id: str) -> RestoreReport:
        """Put a run's files back; skip anything changed since the agent wrote it."""
        entries = [
            entry
            for entry in self._read_manifest()
            if entry.get("run") == run_id
        ]
        if not entries:
            return RestoreReport(run=None, skipped=[(run_id, "unknown run")])
        entries.sort(key=lambda entry: int(entry.get("seq") or 0))
        first: dict[str, dict] = {}
        last: dict[str, dict] = {}
        order: list[str] = []
        for entry in entries:
            path = entry.get("path")
            if not isinstance(path, str) or not path:
                continue
            if path not in first:
                first[path] = entry
                order.append(path)
            last[path] = entry
        report = RestoreReport(
            run=next(
                (info for info in self.runs(limit=1_000_000) if info.run == run_id),
                RunInfo(run=run_id, session=self.session, started="", files=len(order), paths=tuple(order)),
            )
        )
        for path in order:
            reason = self._restore_path(path, first[path], last[path])
            if reason is None:
                report.restored.append(path)
            else:
                report.skipped.append((path, reason))
        return report

    def _restore_path(self, path: str, first: dict, last: dict) -> str | None:
        target = Path(path)
        try:
            current = target.read_bytes()
        except FileNotFoundError:
            current = None
        except OSError:
            return "unreadable"
        last_sha = str(last.get("after_sha256") or "")
        skipped = first.get("skipped")
        if isinstance(skipped, str) and skipped:
            return "backup skipped (too large)" if skipped == "too_large" else f"backup skipped ({skipped})"
        if first.get("kind") == "created":
            if current is None:
                return None
            if _sha(current) != last_sha:
                return "changed since the agent wrote it"
            try:
                target.unlink()
            except OSError:
                return "unreadable"
            return None
        if current is None:
            return "missing"
        if _sha(current) != last_sha:
            return "changed since the agent wrote it"
        digest = first.get("blob")
        if not isinstance(digest, str) or not digest:
            return (
                "backup skipped (too large)"
                if first.get("skipped") == "too_large"
                else "blob missing"
            )
        data = self._read_blob(digest)
        if data is None:
            return "blob missing"
        try:
            self._write_restored(target, data)
        except OSError:
            return "unreadable"
        return None

    @staticmethod
    def _write_restored(target: Path, data: bytes) -> None:
        mode: int | None = None
        try:
            mode = stat.S_IMODE(target.stat().st_mode)
        except OSError:
            pass
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(
            dir=target.parent, prefix=f".{target.name}.", suffix=".zenagent-restore"
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
            if mode is not None:
                os.chmod(temp_name, mode)
            os.replace(temp_name, target)
        except BaseException:
            Path(temp_name).unlink(missing_ok=True)
            raise

    # -- retention ---------------------------------------------------------

    def prune(self, entries: list[dict] | None = None) -> None:
        """Keep the last ``keep_runs`` runs; drop their blobs and manifest lines."""
        if entries is None:
            entries = self._read_manifest()
        if self.policy.keep_runs <= 0:
            return
        by_run: dict[str, list[dict]] = {}
        for entry in entries:
            run = entry.get("run")
            if isinstance(run, str) and run:
                by_run.setdefault(run, []).append(entry)
        if len(by_run) <= self.policy.keep_runs:
            return
        keep = {
            run: None
            for run in list(by_run)[-self.policy.keep_runs :]
        }
        kept = [entry for entry in entries if entry.get("run") in keep]
        self._rewrite_manifest(kept)
        referenced = {
            entry["blob"]
            for entry in kept
            if isinstance(entry.get("blob"), str) and entry["blob"]
        }
        for blob in self.blobs_dir.glob("*/*"):
            if blob.is_file() and blob.name not in referenced:
                blob.unlink(missing_ok=True)


def attach_journal(agent, journal: SnapshotJournal) -> None:
    """Wrap an agent's before hook so journal captures run after approval."""
    previous = getattr(agent, "before_tool_call", None)
    if previous is not None and not _accepts_tool(previous):
        async def before(call, args, tool=None):
            blocked = previous(call, args)
            if inspect.isawaitable(blocked):
                blocked = await blocked
            if blocked:
                return blocked
            await journal.before_tool_call(call, args)
            return None
    else:
        async def before(call, args, tool=None):
            if previous is not None:
                blocked = previous(call, args, tool)
                if inspect.isawaitable(blocked):
                    blocked = await blocked
                if blocked:
                    return blocked
            await journal.before_tool_call(call, args)
            return None

    agent.before_tool_call = before
    agent.after_tool_call = journal.after_tool_call
    agent.subscribe(journal.on_event)
