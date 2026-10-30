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
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from zenagent import project
from zenagent.types import Message, UserMessage, message_from_json

__all__ = [
    "Session",
    "SessionInfo",
    "all_session_dirs",
    "brief",
    "latest_session",
    "list_sessions",
    "read_session_titles",
    "session_path",
    "session_title",
    "sessions_dir",
    "set_session_title",
    "truncate_last_turn",
    "unique_session_path",
]

#: Display names live in a sidecar keyed by session filename: the JSONL stays
#: append-only ``Message.to_json()`` and can still be replayed by other tools.
TITLES_FILENAME = ".titles.json"

#: Stored display names are capped after whitespace normalization.
TITLE_LIMIT = 80

#: Characters that commonly start a Markdown line and carry no title meaning.
_MARKDOWN_PREFIX_CHARS = "#>-*+`"

#: The two listing scopes: the current project, or every project plus legacy.
_SCOPES = ("project", "all")


def sessions_dir() -> Path:
    """Session files for the current project (never created here)."""
    return project.project_sessions_dir()


def all_session_dirs() -> list[Path]:
    """Legacy directory plus every known project's session directory."""
    return project.all_session_dirs()


def session_path(prefix: str = "") -> Path:
    """A fresh session path: ``<date>-<time>[-prefix].jsonl``."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = f"-{prefix}" if prefix else ""
    return sessions_dir() / f"{stamp}{suffix}.jsonl"


def unique_session_path(prefix: str = "") -> Path:
    """Like :func:`session_path`, but never returns an existing file.

    The timestamp has second resolution, so two sessions started in the same
    second would otherwise share a filename -- and a "new" session would load
    the existing transcript instead of starting empty.
    """
    path = session_path(prefix)
    counter = 0
    while path.exists():
        counter += 1
        path = path.with_name(f"{path.stem}-{counter}{path.suffix}")
    return path


def _clean_title_line(line: str) -> str:
    """Flatten one line and drop leading Markdown punctuation."""
    flat = " ".join(line.split())
    return flat.lstrip(_MARKDOWN_PREFIX_CHARS + " ").strip()


def brief(text: str, *, limit: int = 40) -> str:
    """First readable line of ``text``, flattened and shortened for display.

    Leading Markdown markers (``#``, ``>``, ``-``, ``*``, ``+``, backticks) are
    dropped, blank lines skipped, whitespace collapsed, and text beyond
    ``limit`` characters cut with an ellipsis. Text with nothing readable
    becomes ``"(empty)"`` so callers never special-case the empty string.
    """
    cleaned = ""
    for line in text.splitlines():
        cleaned = _clean_title_line(line)
        if cleaned:
            break
    if not cleaned:
        return "(empty)"
    if limit <= 0:
        return ""
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def _titles_path(directory: Path) -> Path:
    return Path(directory) / TITLES_FILENAME


def _project_key_for_directory(directory: Path) -> str | None:
    """Project key owning ``directory``, or ``None`` for anything else."""
    return project.project_key_for_session(Path(directory) / "session.jsonl")


def _project_label(directory: Path) -> str:
    """Display label for a sessions directory; empty for the legacy directory.

    Uses the project's recorded name (``meta.json``) when available and falls
    back to the key, so a listing never shows a blank project for a real one.
    """
    base = Path(directory)
    try:
        if base.resolve() == project.legacy_sessions_dir().resolve():
            return ""
    except OSError:
        pass
    key = _project_key_for_directory(base)
    if key is None:
        return ""
    try:
        data = json.loads(
            (project.projects_root() / key / "meta.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return key
    name = data.get("name") if isinstance(data, dict) else None
    return name if isinstance(name, str) and name else key


def read_session_titles(directory: Path | None = None) -> dict[str, str]:
    """Stored display names by session filename, for one sessions directory.

    ``directory`` defaults to the current project's sessions directory: each
    directory owns its own sidecar, so names cannot leak between projects. A
    missing, unreadable, corrupt, or non-dict sidecar yields ``{}``: naming is
    a convenience, so it must never be able to break session listing or resume.
    """
    base = sessions_dir() if directory is None else Path(directory)
    try:
        data = json.loads(_titles_path(base).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        name: title
        for name, title in data.items()
        if isinstance(name, str) and isinstance(title, str) and title
    }


def _normalize_title(title: str) -> str:
    return " ".join(title.split())[:TITLE_LIMIT]


def _write_session_titles(directory: Path, titles: dict[str, str]) -> None:
    """Replace one directory's sidecar atomically: readers never see a half file."""
    sidecar = _titles_path(directory)
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        dir=sidecar.parent, prefix=f"{sidecar.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(titles, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, sidecar)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def set_session_title(path: Path, title: str) -> str:
    """Persist ``title`` as the display name of the session file at ``path``.

    The sidecar is the one next to the session file (each sessions directory,
    and therefore each project, has its own). The title is whitespace-flattened
    and capped at :data:`TITLE_LIMIT`; a blank title is a caller error (there
    would be nothing to display), so it raises ``ValueError`` and leaves the
    sidecar untouched. The transcript is not read or modified.
    """
    session_file = Path(path)
    normalized = _normalize_title(title)
    if not normalized:
        raise ValueError("session title must not be empty")
    titles = read_session_titles(session_file.parent)
    titles[session_file.name] = normalized
    _write_session_titles(session_file.parent, titles)
    return normalized


def _title_from_messages(messages: list[Message]) -> str:
    """Brief of the first text user message; ``"(empty)"`` when there is none."""
    for message in messages:
        if isinstance(message, UserMessage) and isinstance(message.content, str):
            return brief(message.content)
    return "(empty)"


def _derive_title(path: Path) -> str:
    """Brief of the first text user message; ``"(empty)"`` when there is none."""
    try:
        messages = Session(path).load()
    except OSError:
        return "(empty)"
    return _title_from_messages(messages)


def session_title(path: Path) -> str:
    """Display name for ``path``: a stored override, else the derived brief."""
    session_file = Path(path)
    override = read_session_titles(session_file.parent).get(session_file.name)
    if override:
        return override
    return _derive_title(session_file)


@dataclass
class SessionInfo:
    """Cheap description of a saved session for listings."""

    path: Path
    mtime: float
    messages: int
    title: str
    project: str = ""


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

    def read_info(self, *, titles: dict[str, str] | None = None) -> SessionInfo:
        """Summarize this session; raises ValueError when nothing is readable.

        ``title`` is the stored rename when there is one, otherwise the brief
        derived from the first user message (``"(empty)"`` for a blank session).
        The rename is read from the sidecar next to this session file; callers
        listing many sessions pass that directory's sidecar once via ``titles``.
        ``project`` is left empty here — :func:`list_sessions` fills it in.
        """
        messages = self.load()
        if not messages and self.warnings:
            raise ValueError(f"{self.path.name} is unreadable")
        overrides = (
            read_session_titles(self.path.parent) if titles is None else titles
        )
        return SessionInfo(
            path=self.path,
            mtime=self.path.stat().st_mtime,
            messages=len(messages),
            title=overrides.get(self.path.name) or _title_from_messages(messages),
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
        forked = Session(unique_session_path(f"fork{index}"))
        for message in prefix:
            forked.append(message)
        return forked.path

    def rewrite(self, messages: list[Message]) -> Path:
        """Write ``messages`` to a *new* session file and return its path."""
        rewritten = Session(unique_session_path("rewrite"))
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


def _session_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return float("-inf")


def list_sessions(limit: int = 20, *, scope: str = "project") -> list[SessionInfo]:
    """Saved sessions, newest first, skipping files that cannot be read.

    ``scope="project"`` lists only the current project's sessions (the default,
    so another project or the read-only legacy directory never leaks in).
    ``scope="all"`` merges every known project plus legacy, newest first, and
    labels each entry with its project's display name (empty for legacy).
    Each directory's title sidecar is read once and shared by its sessions.
    """
    if scope not in _SCOPES:
        raise ValueError(f"unknown session scope: {scope!r} (choose project|all)")
    directories = [sessions_dir()] if scope == "project" else all_session_dirs()
    infos: list[SessionInfo] = []
    for directory in directories:
        if not directory.is_dir():
            continue
        titles = read_session_titles(directory)
        label = _project_label(directory)
        for path in directory.glob("*.jsonl"):
            try:
                info = Session(path).read_info(titles=titles)
            except (ValueError, OSError):
                continue
            info.project = label
            infos.append(info)
    infos.sort(key=lambda info: info.mtime, reverse=True)
    return infos[:limit]


def latest_session(*, scope: str = "project") -> Path | None:
    """Most recent session file for ``scope``, if any.

    ``scope="project"`` keeps the historical name ordering (session filenames
    sort chronologically) inside the current project's directory.
    ``scope="all"`` looks across every known project and the legacy directory
    and returns the newest by modification time.
    """
    if scope not in _SCOPES:
        raise ValueError(f"unknown session scope: {scope!r} (choose project|all)")
    if scope == "project":
        directory = sessions_dir()
        if not directory.is_dir():
            return None
        files = sorted(directory.glob("*.jsonl"))
        return files[-1] if files else None
    candidates = [
        path
        for directory in all_session_dirs()
        if directory.is_dir()
        for path in directory.glob("*.jsonl")
    ]
    if not candidates:
        return None
    return max(candidates, key=_session_mtime)
