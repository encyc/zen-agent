"""JSONL session persistence: one message per line, resilient to a torn tail.

The on-disk format *is* ``Message.to_json()`` — there is no extra schema, so a
session file can be inspected with ``jq`` or replayed by any other consumer of
the data model.

Loading is deliberately forgiving: a process killed mid-write leaves a partial
final line, and losing a session because of it would be worse than dropping one
line. Unreadable lines are skipped and reported in ``warnings``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from pyagent.types import Message, UserMessage, message_from_json

__all__ = [
    "Session",
    "SessionInfo",
    "latest_session",
    "list_sessions",
    "session_path",
    "sessions_dir",
    "truncate_last_turn",
]


def sessions_dir() -> Path:
    """Root directory for session files (created on first write)."""
    return Path.home() / ".pyagent" / "sessions"


def session_path(prefix: str = "") -> Path:
    """A fresh session path: ``<date>-<time>[-prefix].jsonl``."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = f"-{prefix}" if prefix else ""
    return sessions_dir() / f"{stamp}{suffix}.jsonl"


@dataclass
class SessionInfo:
    """Cheap description of a saved session for listings."""

    path: Path
    mtime: float
    messages: int
    title: str


@dataclass
class Session:
    """Append-only JSONL transcript on disk.

    ``warnings`` collects notes about skipped lines during :meth:`load`.
    ``messages`` is the cached view; it is kept in sync by :meth:`append`.
    """

    path: Path
    warnings: list[str] = field(default_factory=list)
    _messages: list[Message] | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        self.path = Path(self.path)

    def append(self, message: Message) -> None:
        """Append one message as a JSON line and flush it immediately."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(message.to_json(), ensure_ascii=False))
            handle.write("\n")
            handle.flush()
        if self._messages is not None:
            self._messages.append(message)

    def load(self) -> list[Message]:
        """Read the transcript, skipping unreadable (e.g. torn) lines."""
        messages: list[Message] = []
        self.warnings = []
        if not self.path.is_file():
            self._messages = messages
            return messages
        text = self.path.read_text(encoding="utf-8")
        for lineno, raw in enumerate(text.splitlines(), start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                messages.append(message_from_json(json.loads(line)))
            except (ValueError, TypeError) as exc:
                # Review Focus #5: a torn tail must never crash --resume.
                self.warnings.append(
                    f"{self.path.name}:{lineno}: skipped unreadable line ({exc})"
                )
        self._messages = messages
        return messages

    def read_info(self) -> SessionInfo:
        """Summarize this session; raises ValueError when nothing is readable."""
        messages = self.load()
        if not messages and self.warnings:
            raise ValueError(f"{self.path.name} is unreadable")
        title = ""
        for message in messages:
            if isinstance(message, UserMessage) and isinstance(message.content, str):
                title = " ".join(message.content.split())[:60]
                break
        return SessionInfo(
            path=self.path,
            mtime=self.path.stat().st_mtime,
            messages=len(messages),
            title=title,
        )

    def fork_at(self, index: int) -> Path:
        """Copy the transcript up to (and including) user message ``index``.

        ``index`` is 1-based over user messages. The new file is written; this
        session's file is never modified.
        """
        messages = self.load()
        user_positions = [
            position
            for position, message in enumerate(messages)
            if isinstance(message, UserMessage)
        ]
        if index < 1 or index > len(user_positions):
            raise ValueError(
                f"fork index {index} out of range (1..{len(user_positions)})"
            )
        prefix = messages[: user_positions[index - 1] + 1]
        forked = Session(session_path(f"fork{index}"))
        for message in prefix:
            forked.append(message)
        return forked.path

    def rewrite(self, messages: list[Message]) -> Path:
        """Write ``messages`` to a *new* session file and return its path."""
        rewritten = Session(session_path("rewrite"))
        for message in messages:
            rewritten.append(message)
        return rewritten.path

    @property
    def messages(self) -> list[Message]:
        """The cached transcript (loaded on first access)."""
        if self._messages is None:
            self.load()
        assert self._messages is not None
        return self._messages


def truncate_last_turn(messages: list[Message]) -> list[Message]:
    """Drop the last user turn: that user message and everything after it."""
    for position in range(len(messages) - 1, -1, -1):
        if isinstance(messages[position], UserMessage):
            return list(messages[:position])
    return list(messages)


def list_sessions(limit: int = 20) -> list[SessionInfo]:
    """Saved sessions, newest first, skipping files that cannot be read."""
    directory = sessions_dir()
    if not directory.is_dir():
        return []
    infos: list[SessionInfo] = []
    for path in directory.glob("*.jsonl"):
        try:
            infos.append(Session(path).read_info())
        except (ValueError, OSError):
            continue
    infos.sort(key=lambda info: info.mtime, reverse=True)
    return infos[:limit]


def latest_session() -> Path | None:
    """Most recent session file (names sort chronologically), if any."""
    directory = sessions_dir()
    if not directory.is_dir():
        return None
    files = sorted(directory.glob("*.jsonl"))
    return files[-1] if files else None
