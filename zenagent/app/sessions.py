"""Saved-session browsing and lifecycle operations."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from zenagent.agent.session import Session, list_sessions, session_path
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

    async def new(self) -> Path:
        await self.runtime.switch_session(None)
        return self.current()

    async def fork(self) -> Path:
        """Copy the current transcript into a new session and switch to it."""
        messages = Session(self.current()).load()
        target_path = session_path("fork")
        counter = 0
        while target_path.exists():
            counter += 1
            target_path = target_path.with_name(
                f"{target_path.stem}-{counter}{target_path.suffix}"
            )
        target = Session(target_path)
        for message in messages:
            target.append(message)
        await self.runtime.switch_session(target.path)
        return target.path

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
            self.runtime.session = Session(session_path())
            self.runtime.agent.replace_messages([])
        return destination

    def export(self, path: Path, fmt: str = "md") -> Path:
        return export_session(Path(path), fmt)
