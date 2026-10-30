"""Saved-session browsing and lifecycle operations."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from zenagent.agent.session import (
    Session,
    list_sessions,
    set_session_title,
    truncate_last_turn,
    unique_session_path,
)
from zenagent.app.runtime import Runtime
from zenagent.export import export_session

__all__ = ["SessionEntry", "SessionManager"]


@dataclass
class SessionEntry:
    path: Path
    created: float
    messages: int
    size: int
    current: bool = False
    title: str = ""


class SessionManager:
    """Lists sessions and switches the runtime between them."""

    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime

    def current(self) -> Path:
        """The active session file; created eagerly so listings/fork can read it."""
        path = self.runtime.session.path
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.touch()
        return path

    def entries(self) -> list[SessionEntry]:
        current = self.current()
        entries: list[SessionEntry] = []
        for info in list_sessions(limit=100):
            try:
                size = info.path.stat().st_size
            except OSError:
                size = 0
            entries.append(
                SessionEntry(
                    path=info.path,
                    created=info.mtime,
                    messages=info.messages,
                    size=size,
                    current=info.path == current,
                    title=info.title,
                )
            )
        return entries

    async def resume(self, path: Path) -> None:
        await self.runtime.switch_session(path)

    def rename(self, path: Path, title: str) -> str:
        """Give a saved session a display name without rewriting its JSONL.

        Raises ``ValueError`` for a path that is not a saved session (renaming
        nothing would silently pollute the titles sidecar) or for a blank title.
        Returns the normalized title that was stored.
        """
        target = Path(path)
        if not target.is_file():
            raise ValueError(f"session not found: {target}")
        return set_session_title(target, title)

    async def new(self) -> Path:
        await self.runtime.switch_session(None)
        return self.current()

    async def fork(self) -> Path:
        """Copy the current transcript into a new session and switch to it."""
        messages = Session(self.current()).load()
        target = Session(unique_session_path("fork"))
        for message in messages:
            target.append(message)
        await self.runtime.switch_session(target.path)
        return target.path

    def undo(self) -> tuple[int, Path] | None:
        """Drop the last user turn (message + everything after it), on disk too.

        Mirrors what the REPL has always done: the truncated transcript is
        written to a *new* session file rather than editing the old one, so an
        undo stays recoverable.
        """
        messages = list(self.runtime.agent.messages)
        truncated = truncate_last_turn(messages)
        dropped = len(messages) - len(truncated)
        if dropped == 0:
            return None
        self.runtime.agent.replace_messages(truncated)
        new_path = Session(self.current()).rewrite(truncated)
        self.runtime.session = Session(new_path)
        return dropped, new_path

    def delete(self, path: Path) -> Path:
        """Move a session file to the trash; never unlink a transcript."""
        target = Path(path)
        trash = target.parent / ".trash"
        trash.mkdir(parents=True, exist_ok=True)
        destination = trash / target.name
        if destination.exists():
            destination = trash / f"{target.stem}-{time.strftime('%H%M%S')}.jsonl"
        target.replace(destination)
        if self.runtime.session.path == target:
            self.runtime.session = Session(unique_session_path())
            self.runtime.agent.replace_messages([])
        return destination

    def export(self, path: Path, fmt: str = "md") -> Path:
        return export_session(Path(path), fmt)
