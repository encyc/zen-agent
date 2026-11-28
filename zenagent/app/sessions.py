"""Saved-session browsing and lifecycle operations.

Sessions are project-scoped: the default views read the current project's
directory only, ``scope="all"`` is the explicit cross-project view, and the
legacy flat directory (``~/.zenagent/sessions``) is read-only there until a
session is explicitly adopted into a project.
"""

from __future__ import annotations

import shutil
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import zenagent.agent.session as session_module
from zenagent import project
from zenagent.agent.session import (
    Session,
    list_sessions,
    read_session_pins,
    read_session_titles,
    set_session_archived,
    set_session_pin,
    set_session_title,
    truncate_last_turn,
    unique_session_path,
)
from zenagent.agent.subagents import carry_task_dir, trash_task_dir
from zenagent.app import records
from zenagent.app.runtime import Runtime
from zenagent.export import export_session
from zenagent.i18n import t

__all__ = ["SessionEntry", "SessionManager", "adopt_legacy_session"]


def _inflight_task_ids() -> set[str]:
    """Call ids of background children still running (they pin their store)."""
    from zenagent.agent.subagents_runtime import get_task_manager

    return get_task_manager().running_call_ids()


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
        raise ValueError(t("session.legacy_file", source=source))
    if not _same_directory(source.parent, legacy_dir):
        raise ValueError(
            t("session.legacy_dir", source=source, dir=legacy_dir)
        )
    if _same_directory(source.parent, target_dir):
        raise ValueError(t("session.already_current", source=source))

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
    updated_at: float
    messages: int
    size: int
    created_at: float = 0.0
    model: str = ""
    derived_from: str = ""
    current: bool = False
    title: str = ""
    project: str = ""
    project_path: str = ""
    pinned: bool = False
    archived: bool = False
    archived_at: float = 0.0

    def as_wire(self) -> dict:
        """The JSON shape ``session list`` answers with (paths as strings)."""
        return {
            "path": str(self.path),
            "name": self.path.stem,
            "title": self.title,
            "project": self.project,
            "project_path": self.project_path,
            "messages": self.messages,
            "current": self.current,
            "pinned": self.pinned,
            "archived": self.archived,
            "archived_at": self.archived_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "model": self.model,
            "derived_from": self.derived_from,
        }


class SessionManager:
    """Lists sessions and switches the runtime between them."""

    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime

    def _touch_project_meta(self) -> None:
        """Record the project root/last_used; never fail a session over it."""
        try:
            project.write_project_meta(root=self.runtime.root)
        except OSError:
            pass

    def current(self) -> Path:
        """The active session path; listing it never creates the file.

        Runtime-created sessions are lazy: the file appears with the first
        message, so a read-only path query must not materialize one.
        """
        self._touch_project_meta()
        return self.runtime.session.path

    def _materialize_current(self, target: Path) -> None:
        """An explicit rename/pin is intent to keep the session: give it a file."""
        if target == self.runtime.session.path and not target.exists():
            self.runtime.session.materialize()

    def entries(
        self,
        *,
        scope: str = "project",
        limit: int | None = None,
        live: Iterable[Path] = (),
        archived: str = "exclude",
    ) -> list[SessionEntry]:
        """Rows for the session list, newest first with pinned rows on top.

        ``limit`` caps the saved transcripts read from disk; ``None`` reads
        every one, because the sidebar pages each project itself and a silent
        global cap only loses the tail. ``live`` names sessions other runtimes
        hold right now — a run streaming in a sibling slot has no transcript
        yet — so they keep a row instead of vanishing from the list.
        ``archived`` filters by the archive sidecar (``list_sessions``):
        ``"exclude"`` is the sidebar view, ``"only"`` the settings view. The
        placeholder and live-row bookkeeping only makes sense for the active
        listing, so it runs for ``"exclude"`` alone.
        """
        current = self.current()
        entries: list[SessionEntry] = []
        pins_by_dir: dict[Path, set[str]] = {}
        roots_by_dir: dict[Path, str] = {}
        for info in list_sessions(limit=limit, scope=scope, archived=archived):
            try:
                size = info.path.stat().st_size
            except OSError:
                size = 0
            directory = info.path.parent
            if directory not in pins_by_dir:
                pins_by_dir[directory] = read_session_pins(directory)
                roots_by_dir[directory] = project.project_root_for_sessions_dir(
                    directory
                )
            entries.append(
                SessionEntry(
                    path=info.path,
                    updated_at=info.mtime,
                    messages=info.messages,
                    size=size,
                    created_at=info.created_at,
                    model=info.model,
                    derived_from=info.derived_from,
                    current=info.path == current,
                    title=info.title,
                    project=info.project,
                    project_path=roots_by_dir[directory],
                    pinned=info.path.name in pins_by_dir[directory],
                    archived=info.archived,
                    archived_at=info.archived_at,
                )
            )
        if archived != "exclude":
            # A pure listing (the settings view): no placeholder for the
            # unsaved current session, no live rows — those describe the
            # active conversation, which an archived listing does not show.
            return entries
        # Pinned first, otherwise newest-first exactly as list_sessions returned
        # (sorted() is stable, so no time order is lost inside each group).
        entries.sort(key=lambda entry: not entry.pinned)
        if not any(entry.current for entry in entries):
            entries.insert(
                self._unsaved_position(entries),
                self._placeholder(current, current=True),
            )
        rows = self._live_rows(entries, current, live)
        if rows:
            # Just after the pinned block, and after the on-screen session's
            # own placeholder: a background conversation never outranks it.
            position = self._unsaved_position(entries)
            if position < len(entries) and entries[position].current:
                position += 1
            entries[position:position] = rows
        return entries

    @staticmethod
    def _unsaved_position(entries: list[SessionEntry]) -> int:
        """Index just after the pinned block, where a new conversation belongs."""
        for position, entry in enumerate(entries):
            if not entry.pinned:
                return position
        return len(entries)

    def _live_rows(
        self, entries: list[SessionEntry], current: Path, live: Iterable[Path]
    ) -> list[SessionEntry]:
        """Rows for live sessions this listing would otherwise drop.

        A run streaming in a sibling slot has no transcript on disk yet (the
        file lands at ``agent_end``), so a listing that reads only files — plus
        this runtime's own current session — loses it the moment the person
        switches away: the run keeps going, but the sidebar cannot find it.
        Never writes; the row gives way to the real transcript when it lands.
        """
        listed = {entry.path for entry in entries}
        rows: list[SessionEntry] = []
        for path in live:
            candidate = Path(path)
            if candidate == current or candidate in listed:
                continue
            rows.append(self._placeholder(candidate, current=False))
        return rows

    def _placeholder(self, path: Path, *, current: bool) -> SessionEntry:
        """A row for a session with no transcript on disk yet.

        A new conversation only materializes on its first message, and a run
        in progress is written at ``agent_end``: without this row the session
        someone is talking in cannot be found or re-selected in a session list.
        Nothing is written to disk -- this row lives for the duration of the
        list call.
        """
        directory = path.parent
        root = project.project_root_for_sessions_dir(directory)
        meta = project.load_project_meta(root=root) if root else None
        now = time.time()
        return SessionEntry(
            path=path,
            updated_at=now,
            messages=0,
            size=0,
            created_at=now,
            title=read_session_titles(directory).get(path.name)
            or t("session.untitled"),
            project=meta.name if meta is not None else "",
            project_path=root,
            pinned=False,
            current=current,
        )

    def set_pin(self, path: Path, pinned: bool) -> bool:
        """Pin/unpin one saved session; ``ValueError`` for a non-session path."""
        target = Path(path)
        self._materialize_current(target)
        return set_session_pin(target, pinned)

    def toggle_pin(self, path: Path) -> bool:
        """Flip the pinned flag of one saved session; returns the new state."""
        target = Path(path)
        self._materialize_current(target)
        if not target.is_file():
            raise ValueError(t("session.not_found", target=target))
        currently = target.name in read_session_pins(target.parent)
        return set_session_pin(target, not currently)

    def set_archived(self, path: Path, archived: bool) -> float:
        """Archive/restore one saved session; returns its archived-at stamp.

        Delegates to the kernel sidecar, whose validation is the contract:
        a lazy (unsaved) session or a legacy-directory file is refused. The
        transcript is never touched, so a session open in another slot keeps
        working; no materialize — archiving an unsaved conversation is a
        caller error, not an intent to keep it.
        """
        return set_session_archived(Path(path), archived)

    async def resume(self, path: Path) -> None:
        await self.runtime.switch_session(path)

    def rename(self, path: Path, title: str) -> str:
        """Give a saved session a display name without rewriting its JSONL.

        Raises ``ValueError`` for a path that is not a saved session (renaming
        nothing would silently pollute the titles sidecar) or for a blank title.
        Returns the normalized title that was stored.
        """
        target = Path(path)
        is_current = target == self.runtime.session.path
        if not target.is_file() and not is_current:
            raise ValueError(f"session not found: {target}")
        # Validate the title before materializing: a rejected rename must not
        # turn an unsaved conversation into an empty file.
        stored = set_session_title(target, title)
        if is_current and not target.exists():
            self.runtime.session.materialize()
        return stored

    async def new(self) -> Path:
        self._touch_project_meta()
        await self.runtime.switch_session(None)
        return self.current()

    async def fork(self) -> Path:
        """Copy the current transcript into a new session and switch to it."""
        self._touch_project_meta()
        source = self.current()
        source_session = Session(source)
        messages = source_session.load()
        target = Session(
            unique_session_path("fork", root=self.runtime.root),
            derived_from=source.stem,
            cwd=self.runtime.root,
        )
        for message in messages:
            target._append_sync(message)
        for record in source_session.carry_records(
            messages, keep=records.keep_for_rewrite(keep_goal=False)
        ):
            target.append_meta(record)
        # Historical subagent transcripts follow the history they describe.
        carry_task_dir(source, target.path, skip=_inflight_task_ids())
        await self.runtime.switch_session(target.path)
        return target.path

    def adopt(self, path: Path) -> Path:
        """Move a legacy session into this project; see :func:`adopt_legacy_session`."""
        return adopt_legacy_session(path, cwd=self.runtime.root)

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
        source = self.current()
        new_path = Session(source).rewrite(
            truncated,
            keep=records.keep_for_rewrite(),
            root=self.runtime.root,
        )
        # The truncated history keeps its subagent transcripts.
        carry_task_dir(source, new_path, skip=_inflight_task_ids())
        self.runtime.session = records.open_session(
            new_path, cwd=self.runtime.root
        )
        return dropped, new_path

    def delete(self, path: Path) -> Path:
        """Move a session file to the trash; never unlink a transcript.

        A lazy current session has no file yet: deleting it only starts a new
        one, and there is nothing to move.
        """
        target = Path(path)
        trash = target.parent / ".trash"
        destination = target
        if target.exists():
            trash.mkdir(parents=True, exist_ok=True)
            destination = trash / target.name
            if destination.exists():
                destination = trash / f"{target.stem}-{time.strftime('%H%M%S')}.jsonl"
            target.replace(destination)
        trash_task_dir(target, trash)
        if self.runtime.session.path == target:
            self.runtime.use_new_session()
            self.runtime.agent.replace_messages([])
        return destination

    def export(self, path: Path, fmt: str = "md") -> Path:
        return export_session(Path(path), fmt)
