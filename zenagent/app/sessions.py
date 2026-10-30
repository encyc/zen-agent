"""Saved-session browsing and lifecycle operations.

Sessions are project-scoped: the default views read the current project's
directory only, ``scope="all"`` is the explicit cross-project view, and the
legacy flat directory (``~/.zenagent/sessions``) is read-only there until a
session is explicitly adopted into a project.
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass
from pathlib import Path

import zenagent.agent.session as session_module
from zenagent import project
from zenagent.agent.session import (
    Session,
    list_sessions,
    read_session_titles,
    set_session_title,
    truncate_last_turn,
    unique_session_path,
)
from zenagent.app.runtime import Runtime
from zenagent.export import export_session

__all__ = ["SessionEntry", "SessionManager", "adopt_legacy_session"]


def _same_directory(one: Path, two: Path) -> bool:
    try:
        return Path(one).expanduser().resolve() == Path(two).expanduser().resolve()
    except OSError:
        return False


def _free_session_path(directory: Path, name: str) -> Path:
    """``directory/name``, with ``-1``/``-2``/... appended while it exists."""
    destination = Path(directory) / name
    stem, suffix = Path(name).stem, Path(name).suffix
    counter = 0
    while destination.exists():
        counter += 1
        destination = Path(directory) / f"{stem}-{counter}{suffix}"
    return destination


def adopt_legacy_session(path: Path, *, cwd: Path | None = None) -> Path:
    """Move a legacy transcript into the current project's sessions directory.

    Legacy (``~/.zenagent/sessions``) sessions are unscoped and read-only; this
    is the explicit action that brings one into a project. The file is moved
    (never copied and never deleted), a name clash gets a ``-1``/``-2`` suffix
    instead of overwriting, and a display name stored in the legacy sidecar is
    migrated to the destination directory's sidecar (the legacy sidecar itself
    is left untouched, as legacy data is read-only).

    Raises ``ValueError`` for anything that is not a legacy ``*.jsonl`` file --
    including a transcript already inside the current project -- so repeating
    an adopt fails loudly instead of silently doing nothing.
    """
    source = Path(path).expanduser()
    legacy_dir = project.legacy_sessions_dir()
    target_dir = (
        project.project_sessions_dir(cwd)
        if cwd is not None
        else session_module.sessions_dir()
    )
    if source.suffix != ".jsonl" or not source.is_file():
        raise ValueError(f"not a legacy session file: {source}")
    if not _same_directory(source.parent, legacy_dir):
        raise ValueError(
            f"not a legacy session file: {source} (expected under {legacy_dir})"
        )
    if _same_directory(source.parent, target_dir):
        raise ValueError(f"session already belongs to the current project: {source}")

    title = read_session_titles(legacy_dir).get(source.name)
    target_dir.mkdir(parents=True, exist_ok=True)
    destination = _free_session_path(target_dir, source.name)
    shutil.move(str(source), str(destination))

    # Preserve a project-local name on conflict: adopting must never clobber
    # the target directory's own metadata.
    if title and destination.name not in read_session_titles(target_dir):
        set_session_title(destination, title)
    return destination


@dataclass
class SessionEntry:
    path: Path
    created: float
    messages: int
    size: int
    current: bool = False
    title: str = ""
    project: str = ""


class SessionManager:
    """Lists sessions and switches the runtime between them."""

    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime

    def _touch_project_meta(self) -> None:
        """Record the project root/last_used; never fail a session over it."""
        try:
            project.write_project_meta()
        except OSError:
            pass

    def current(self) -> Path:
        """The active session file; created eagerly so listings/fork can read it."""
        self._touch_project_meta()
        path = self.runtime.session.path
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.touch()
        return path

    def entries(self, *, scope: str = "project") -> list[SessionEntry]:
        current = self.current()
        entries: list[SessionEntry] = []
        for info in list_sessions(limit=100, scope=scope):
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
                    project=info.project,
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
        self._touch_project_meta()
        await self.runtime.switch_session(None)
        return self.current()

    async def fork(self) -> Path:
        """Copy the current transcript into a new session and switch to it."""
        self._touch_project_meta()
        messages = Session(self.current()).load()
        target = Session(unique_session_path("fork"))
        for message in messages:
            target.append(message)
        await self.runtime.switch_session(target.path)
        return target.path

    def adopt(self, path: Path) -> Path:
        """Move a legacy session into this project; see :func:`adopt_legacy_session`."""
        return adopt_legacy_session(path)

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
