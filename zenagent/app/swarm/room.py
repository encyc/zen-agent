"""Append-only room and task registry backing the swarm runtime.

Both logs are JSONL: the room records every message, the task log records each
state transition (last line per task wins). Replay rebuilds the in-memory
index; a malformed line is skipped with a warning so a hand edit cannot brick
the room.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

__all__ = [
    "TERMINAL",
    "RoomMessage",
    "RoomRef",
    "SwarmStore",
    "TaskRecord",
    "is_terminal",
    "parse_mentions",
]

RECORD_VERSION = 1
AUTHOR_KINDS = ("user", "agent", "system")
TERMINAL = {"done", "failed", "cancelled"}
TRANSITIONS = {
    "queued": {"running", "cancelled", "failed"},
    "running": {"done", "failed", "cancelled"},
}
MENTION_RE = re.compile(r"(?<![\w@])@([a-z0-9][a-z0-9._-]*)")


@dataclass
class RoomRef:
    """A snapshot of the message this one quotes: the task prompt must be
    self-contained, and the quote card must render when the source is outside
    the loaded window."""

    id: str
    author_kind: str
    author_id: str
    text: str


@dataclass
class RoomMessage:
    id: str
    ts: int
    author_kind: str
    author_id: str
    text: str
    mentions: list[str] = field(default_factory=list)
    task_id: str | None = None
    chain_depth: int = 0
    ref: RoomRef | None = None
    images: list[str] = field(default_factory=list)


@dataclass
class TaskRecord:
    id: str
    assignee: str
    state: str = "queued"
    source_message_id: str | None = None
    created_by_kind: str = "user"
    created_by_id: str = ""
    chain_id: str = ""
    chain_depth: int = 0
    chain_cost: float = 0.0
    created_at: int = 0
    updated_at: int = 0
    result_message_id: str | None = None
    reason: str | None = None
    derived_from: str | None = None
    #: The work item this round belongs to. ``None`` only for ad-hoc history
    #: (records written before work items existed) and rounds continued from it.
    work_item_id: str | None = None
    #: What this round alone spent. ``chain_cost`` is a cumulative snapshot for
    #: its chain, so summing it across rounds double-counts ancestors.
    run_cost: float = 0.0


def is_terminal(state: str) -> bool:
    return state in TERMINAL


def parse_mentions(text: str) -> list[str]:
    return list(dict.fromkeys(match.group(1) for match in MENTION_RE.finditer(text)))


class SwarmStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.messages: list[RoomMessage] = []
        self.tasks: dict[str, TaskRecord] = {}
        #: Total spend per chain, summed from each run's increment. Task
        #: records carry snapshots, which go stale as soon as a sibling
        #: branch spends; the router gates on this aggregate instead.
        self.chain_spend: dict[str, float] = {}
        self.warnings: list[str] = []
        self.loaded = False
        self._message_ids: set[str] = set()
        self._lock = threading.Lock()

    @property
    def room_path(self) -> Path:
        return self.root / "room.jsonl"

    @property
    def tasks_path(self) -> Path:
        return self.root / "tasks.jsonl"

    def load(self) -> None:
        with self._lock:
            self.messages = []
            self.tasks = {}
            self.chain_spend = {}
            self._message_ids = set()
            self.warnings = []
            self._repair_tail(self.room_path)
            self._repair_tail(self.tasks_path)
            self._replay(self.room_path, self._apply_message)
            self._replay(self.tasks_path, self._apply_task)
            self.loaded = True

    def append_message(self, message: RoomMessage) -> None:
        with self._lock:
            self._append_line(self.room_path, _message_record(message))
            self._remember_message(message)

    def append_task(self, task: TaskRecord) -> None:
        with self._lock:
            previous = self.tasks.get(task.id)
            if previous is None:
                if task.state != "queued":
                    raise ValueError(
                        f"task {task.id} must start queued, got {task.state!r}"
                    )
            else:
                allowed = TRANSITIONS.get(previous.state, set())
                if task.state not in allowed:
                    raise ValueError(
                        f"task {task.id}: illegal transition "
                        f"{previous.state!r} -> {task.state!r}"
                    )
            self._append_line(self.tasks_path, _task_record(task))
            self._record_spend(previous, task)
            self.tasks[task.id] = task

    def _record_spend(self, previous: TaskRecord | None, task: TaskRecord) -> None:
        if previous is None:
            # A task's first record carries the chain spend known when it was
            # created (a retry reuses its source's total). Take it as a floor:
            # ancestors and siblings already counted are not added twice.
            self.chain_spend[task.chain_id] = max(
                self.chain_spend.get(task.chain_id, 0.0), task.chain_cost
            )
            return
        delta = task.chain_cost - previous.chain_cost
        if delta:
            self.chain_spend[task.chain_id] = round(
                self.chain_spend.get(task.chain_id, 0.0) + delta, 6
            )

    def reconcile(self) -> list[RoomMessage]:
        interrupted = [
            task for task in list(self.tasks.values()) if not is_terminal(task.state)
        ]
        if not interrupted:
            return []
        now = int(time.time())
        for task in interrupted:
            self.append_task(
                replace(task, state="failed", reason="interrupted", updated_at=now)
            )
        message = RoomMessage(
            id=self.new_id("m"),
            ts=now,
            author_kind="system",
            author_id="system",
            text=f"Host restarted: {len(interrupted)} task(s) marked interrupted",
        )
        self.append_message(message)
        return [message]

    def new_id(self, prefix: str) -> str:
        return f"{prefix}_{time.time_ns():x}{secrets.token_hex(4)}"

    def _remember_message(self, message: RoomMessage) -> None:
        if message.id in self._message_ids:
            return
        self._message_ids.add(message.id)
        self.messages.append(message)

    def _apply_message(self, raw: dict) -> None:
        author = raw.get("author")
        if not isinstance(author, dict):
            author = {}
        message = RoomMessage(
            id=str(raw["id"]),
            ts=int(raw.get("ts") or 0),
            author_kind=str(author.get("kind") or "system"),
            author_id=str(author.get("id") or ""),
            text=str(raw.get("text") or ""),
            mentions=[str(entry) for entry in raw.get("mentions") or []],
            task_id=raw.get("taskId"),
            chain_depth=int(raw.get("chainDepth") or 0),
            ref=_parse_ref(raw.get("ref")),
            images=_parse_images(raw.get("images")),
        )
        self._remember_message(message)

    def _apply_task(self, raw: dict) -> None:
        created_by = raw.get("createdBy")
        if not isinstance(created_by, dict):
            created_by = {}
        record = TaskRecord(
            id=str(raw["id"]),
            assignee=str(raw["assignee"]),
            state=str(raw["state"]),
            source_message_id=raw.get("sourceMessageId"),
            created_by_kind=str(created_by.get("kind") or "user"),
            created_by_id=str(created_by.get("id") or ""),
            chain_id=str(raw.get("chainId") or ""),
            chain_depth=int(raw.get("chainDepth") or 0),
            chain_cost=float(raw.get("chainCost") or 0.0),
            created_at=int(raw.get("createdAt") or 0),
            updated_at=int(raw.get("updatedAt") or 0),
            result_message_id=raw.get("resultMessageId"),
            reason=raw.get("reason"),
            derived_from=raw.get("derivedFrom"),
            work_item_id=raw.get("workItemId"),
            run_cost=float(raw.get("runCost") or 0.0),
        )
        self._record_spend(self.tasks.get(record.id), record)
        self.tasks[record.id] = record

    def _replay(self, path: Path, apply) -> None:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return
        except OSError as exc:
            self.warnings.append(f"{path.name}: {exc}")
            return
        for line in lines:
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except ValueError:
                self.warnings.append(f"{path.name}: skipped malformed line")
                continue
            if not isinstance(raw, dict):
                continue
            try:
                apply(raw)
            except (KeyError, TypeError, ValueError) as exc:
                self.warnings.append(f"{path.name}: skipped record: {exc}")

    def _repair_tail(self, path: Path) -> None:
        # A crash mid-append can leave a partial last line. Drop everything
        # after the final newline so the next append starts on a clean boundary
        # instead of concatenating onto the torn record.
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            return
        except OSError as exc:
            self.warnings.append(f"{path.name}: {exc}")
            return
        if not data or data.endswith(b"\n"):
            return
        cut = data.rfind(b"\n") + 1
        with path.open("r+b") as handle:
            handle.truncate(cut)
            handle.flush()
            os.fsync(handle.fileno())

    def _append_line(self, path: Path, payload: dict) -> None:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())


def _parse_images(raw) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw if isinstance(item, str)]


def _parse_ref(raw) -> RoomRef | None:
    if not isinstance(raw, dict):
        return None
    author = raw.get("author")
    if not isinstance(author, dict):
        author = {}
    ref_id = raw.get("id")
    if not isinstance(ref_id, str) or not ref_id:
        return None
    return RoomRef(
        id=ref_id,
        author_kind=str(author.get("kind") or "system"),
        author_id=str(author.get("id") or ""),
        text=str(raw.get("text") or ""),
    )


def _message_record(message: RoomMessage) -> dict:
    record = {
        "type": "message",
        "version": RECORD_VERSION,
        "id": message.id,
        "ts": message.ts,
        "author": {"kind": message.author_kind, "id": message.author_id},
        "text": message.text,
        "mentions": list(message.mentions),
        "taskId": message.task_id,
        "chainDepth": message.chain_depth,
    }
    if message.ref is not None:
        record["ref"] = {
            "id": message.ref.id,
            "author": {"kind": message.ref.author_kind, "id": message.ref.author_id},
            "text": message.ref.text,
        }
    if message.images:
        record["images"] = list(message.images)
    return record


def _task_record(task: TaskRecord) -> dict:
    return {
        "type": "task",
        "version": RECORD_VERSION,
        "id": task.id,
        "assignee": task.assignee,
        "state": task.state,
        "sourceMessageId": task.source_message_id,
        "createdBy": {"kind": task.created_by_kind, "id": task.created_by_id},
        "chainId": task.chain_id,
        "chainDepth": task.chain_depth,
        "chainCost": task.chain_cost,
        "createdAt": task.created_at,
        "updatedAt": task.updated_at,
        "resultMessageId": task.result_message_id,
        "reason": task.reason,
        "derivedFrom": task.derived_from,
        "workItemId": task.work_item_id,
        "runCost": task.run_cost,
    }
