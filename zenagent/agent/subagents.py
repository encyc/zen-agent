"""Session-scoped subagent transcripts: one JSONL file per ``task`` call.

Layout mirrors :mod:`zenagent.agent.artifacts` -- one directory per session
under a shared parent -- because this is "one session ↔ many files"::

    <sessions dir>/tasks/<session stem>/<call_id>.jsonl

Each file is header/body/footer: a ``meta`` line, the child's
``Message.to_json()`` lines and an ``end`` line. A file without ``end`` is an
interrupted run and stays readable; unreadable lines are skipped like a torn
session tail. Writing is best-effort: any ``OSError`` degrades that call to
memory-only (artifacts set the same precedent).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from zenagent.types import Message, message_from_json

__all__ = [
    "MAX_TASK_FILES",
    "MAX_TASK_FILE_BYTES",
    "MAX_TASK_TOTAL_BYTES",
    "TASKS_DIRNAME",
    "SubagentMeta",
    "SubagentStore",
    "carry_task_dir",
    "task_dir",
    "trash_task_dir",
]

#: Directory under the sessions dir that groups every session's transcripts.
TASKS_DIRNAME = "tasks"

#: Files kept per session before the oldest are pruned.
MAX_TASK_FILES = 32
#: Total transcript bytes kept per session before the oldest are pruned.
MAX_TASK_TOTAL_BYTES = 8 * 1024 * 1024
#: One transcript is truncated (and marked) past this size.
MAX_TASK_FILE_BYTES = 2 * 1024 * 1024

#: How much of a file's tail is scanned for the ``end``/``truncated`` markers.
_TAIL_BYTES = 4096

#: Characters that would confuse a filename are folded to ``_``.
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")

_TASK_STATUSES = ("ok", "error", "aborted", "truncated", "interrupted")


def task_dir(session_path: str | Path) -> Path:
    """``<sessions dir>/tasks/<session stem>/`` for one session file."""
    path = Path(session_path)
    return path.parent / TASKS_DIRNAME / path.stem


def carry_task_dir(old_session: str | Path, new_session: str | Path) -> None:
    """Copy a session's transcripts to its continuation (fork/undo/retry)."""
    source = task_dir(old_session)
    if not source.is_dir():
        return
    try:
        target = task_dir(new_session)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target, dirs_exist_ok=True)
    except OSError:
        return


def trash_task_dir(session_path: str | Path, trash_dir: str | Path) -> Path | None:
    """Move a deleted session's transcripts into the trash; never unlink them."""
    source = task_dir(session_path)
    if not source.is_dir():
        return None
    try:
        trash = Path(trash_dir)
        trash.mkdir(parents=True, exist_ok=True)
        stem = Path(session_path).stem
        target = trash / f"{stem}.tasks"
        if target.exists():
            target = trash / f"{stem}-{int(time.time())}.tasks"
        shutil.move(str(source), str(target))
        return target
    except OSError:
        return None


@dataclass(frozen=True)
class SubagentMeta:
    """The header/footer facts of one ``task`` transcript."""

    call_id: str
    name: str
    model: str
    started_at: float
    status: str = "running"
    duration_ms: int = 0
    cost: float = 0.0
    tokens: int = 0
    truncated: bool = False


class SubagentStore:
    """One session's subagent transcripts (memory-only without a session)."""

    def __init__(self, session_path: str | Path | None) -> None:
        self._session: Path | None = None
        self._directory: Path | None = None
        self._written: dict[str, int] = {}
        self._stopped: set[str] = set()
        self._memory: dict[str, dict] = {}
        self.bind_session(session_path)

    # -- binding ------------------------------------------------------------

    def bind_session(self, session_path: str | Path | None) -> None:
        """Point the store at another session (``None`` = memory-only)."""
        self._session = Path(session_path) if session_path is not None else None
        self._directory = (
            task_dir(self._session) if self._session is not None else None
        )
        self._written.clear()
        self._stopped.clear()
        self._memory.clear()

    # -- writing ------------------------------------------------------------

    def begin(self, call_id: str, name: str, model: str) -> None:
        """Start a transcript: write the header line (or keep it in memory)."""
        started_at = time.time()
        if self._directory is None:
            self._memory[call_id] = {
                "name": name,
                "model": model,
                "started_at": started_at,
                "status": "running",
                "duration_ms": 0,
                "cost": 0.0,
                "tokens": 0,
            }
            return
        path = self._path(call_id)
        try:
            self._directory.mkdir(parents=True, exist_ok=True)
            _chmod(self._directory, 0o700)
            path.write_text("", encoding="utf-8")
            _chmod(path, 0o600)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "type": "meta",
                            "call_id": call_id,
                            "name": name,
                            "model": model,
                            "started_at": started_at,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
            self._written[call_id] = 0
            self._prune()
        except OSError:
            self._stopped.add(call_id)

    def append(self, call_id: str, messages: list[Message]) -> None:
        """Append the messages the transcript has grown by since the last call."""
        if self._directory is None:
            return
        path = self._path(call_id)
        if path is None or call_id in self._stopped or not path.is_file():
            return
        written = self._written.get(call_id, 0)
        pending = messages[written:]
        if not pending:
            return
        try:
            lines = [
                json.dumps(message.to_json(), ensure_ascii=False) for message in pending
            ]
            size = path.stat().st_size + sum(len(line) + 1 for line in lines)
            if size > MAX_TASK_FILE_BYTES:
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps({"type": "truncated"}) + "\n")
                self._stopped.add(call_id)
                return
            with path.open("a", encoding="utf-8") as handle:
                for line in lines:
                    handle.write(line + "\n")
                handle.flush()
            self._written[call_id] = len(messages)
        except OSError:
            self._stopped.add(call_id)

    def finish(
        self,
        call_id: str,
        *,
        status: str,
        duration_ms: int,
        cost: float,
        tokens: int,
    ) -> None:
        """Write the footer line (idempotent; a second call is ignored)."""
        if call_id in self._stopped:
            return
        if self._directory is None:
            memory = self._memory.get(call_id)
            if memory is not None:
                memory.update(
                    status=status,
                    duration_ms=duration_ms,
                    cost=cost,
                    tokens=tokens,
                )
            return
        path = self._path(call_id)
        if path is None or not path.is_file():
            return
        try:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "type": "end",
                            "status": status,
                            "duration_ms": duration_ms,
                            "cost": cost,
                            "tokens": tokens,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                handle.flush()
        except OSError:
            self._stopped.add(call_id)
        self._prune()

    # -- reading ------------------------------------------------------------

    def list(self) -> list[SubagentMeta]:
        """Every known transcript, newest first (disk wins over memory)."""
        metas: list[SubagentMeta] = []
        if self._directory is not None and self._directory.is_dir():
            for path in self._directory.glob("*.jsonl"):
                meta = _read_meta(path)
                if meta is not None:
                    metas.append(meta)
        for call_id, memory in self._memory.items():
            metas.append(
                SubagentMeta(
                    call_id=call_id,
                    name=str(memory.get("name") or ""),
                    model=str(memory.get("model") or ""),
                    started_at=float(memory.get("started_at") or 0.0),
                    status=str(memory.get("status") or "running"),
                    duration_ms=int(memory.get("duration_ms") or 0),
                    cost=float(memory.get("cost") or 0.0),
                    tokens=int(memory.get("tokens") or 0),
                )
            )
        metas.sort(key=lambda meta: meta.started_at, reverse=True)
        return metas

    def load(self, call_id: str) -> list[Message] | None:
        """The child's messages, or None when no transcript exists."""
        path = self._path(call_id)
        if path is None or not path.is_file():
            return None
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return None
        messages: list[Message] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except ValueError:
                continue
            if not isinstance(payload, dict):
                continue
            if payload.get("type") in ("meta", "end", "truncated"):
                continue
            try:
                messages.append(message_from_json(payload))
            except (ValueError, TypeError):
                continue
        return messages

    # -- internals ----------------------------------------------------------

    def _path(self, call_id: str) -> Path | None:
        if self._directory is None:
            return None
        safe = _UNSAFE.sub("_", str(call_id)).strip("._-")[:80] or "task"
        return self._directory / f"{safe}.jsonl"

    def _prune(self) -> None:
        directory = self._directory
        if directory is None:
            return
        try:
            files = [path for path in directory.glob("*.jsonl") if path.is_file()]
            if not files:
                return
            entries = sorted(files, key=lambda path: path.stat().st_mtime_ns)
            total = sum(path.stat().st_size for path in entries)
            while entries and (
                len(entries) > MAX_TASK_FILES or total > MAX_TASK_TOTAL_BYTES
            ):
                victim = entries.pop(0)
                total -= victim.stat().st_size
                victim.unlink(missing_ok=True)
        except OSError:
            return


def _read_meta(path: Path) -> SubagentMeta | None:
    """Parse one transcript's header + tail; None when the header is unusable."""
    try:
        with path.open("rb") as handle:
            header = handle.readline().decode("utf-8", "replace")
            size = path.stat().st_size
            if size > _TAIL_BYTES:
                handle.seek(size - _TAIL_BYTES)
            tail = handle.read().decode("utf-8", "replace")
    except OSError:
        return None
    try:
        meta = json.loads(header)
    except ValueError:
        return None
    if not isinstance(meta, dict) or meta.get("type") != "meta":
        return None

    end, truncated = _scan_tail(tail)
    status = str(meta.get("status") or "")
    if end is not None:
        status = str(end.get("status") or "ok")
        if status not in _TASK_STATUSES:
            status = "ok"
    elif status == "":
        status = "interrupted"
    return SubagentMeta(
        call_id=str(meta.get("call_id") or path.stem),
        name=str(meta.get("name") or ""),
        model=str(meta.get("model") or ""),
        started_at=float(meta.get("started_at") or 0.0),
        status=status,
        duration_ms=int(end.get("duration_ms") or 0) if end else 0,
        cost=float(end.get("cost") or 0.0) if end else 0.0,
        tokens=int(end.get("tokens") or 0) if end else 0,
        truncated=truncated,
    )


def _scan_tail(tail: str) -> tuple[dict | None, bool]:
    """Last ``end`` payload and whether the tail shows damage/truncation."""
    truncated = False
    for raw in reversed(tail.splitlines()):
        line = raw.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except ValueError:
            truncated = True
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("type") == "truncated":
            truncated = True
            continue
        if payload.get("type") == "end":
            return payload, truncated
    return None, truncated


def _chmod(path: Path, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except OSError:
        return
