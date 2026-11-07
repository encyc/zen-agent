"""Project identity and the on-disk layout for per-project state.

The split follows omp/pi: project *inputs* that should travel with the repo live
in ``<root>/.zenagent/`` (config, MCP definitions, skills, commands), while
machine-local runtime state lives under ``~/.zenagent/``:

    ~/.zenagent/projects/<slug>-<hash8>/   ← per-project runtime state
        meta.json                          canonical root, name, last_used
        sessions/*.jsonl                   transcripts
        sessions/.titles.json              display-name sidecar

The project root is the nearest ancestor containing ``.git`` (a directory for a
normal checkout, a file for a worktree); non-git directories are their own
project. The key is ``<basename>-<sha256(canonical root)[:8]>``: readable in a
listing and collision-free for same-named repos.

This module depends only on the standard library so the kernel (``agent/``)
can import it without pulling in the app layer.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

__all__ = [
    "ProjectMeta",
    "ProjectRelocation",
    "all_session_dirs",
    "find_project_root",
    "legacy_sessions_dir",
    "load_project_meta",
    "project_backups_dir",
    "project_config_path",
    "project_dir",
    "project_key",
    "project_key_for_session",
    "project_mcp_path",
    "project_root_for_sessions_dir",
    "project_sessions_dir",
    "projects_root",
    "relocate_project",
    "write_project_meta",
    "zenagent_home",
]

_HASH_LENGTH = 8
_SLUG_KEEP = re.compile(r"[^a-z0-9._-]+")


def zenagent_home() -> Path:
    """Machine-wide state directory (tests monkeypatch this single point)."""
    return Path.home() / ".zenagent"


def projects_root() -> Path:
    """Root holding one directory per known project."""
    return zenagent_home() / "projects"


def legacy_sessions_dir() -> Path:
    """Pre-project sessions: flat ``~/.zenagent/sessions/*.jsonl`` (read-only)."""
    return zenagent_home() / "sessions"


def find_project_root(cwd: Path | str | None = None) -> Path:
    """Nearest ancestor with a ``.git`` entry; fall back to the directory itself."""
    start = Path(cwd) if cwd is not None else Path.cwd()
    start = start.expanduser().resolve()
    if start.is_file():
        start = start.parent
    for candidate in (start, *start.parents):
        if (candidate / ".git").exists():
            return candidate
    return start


def _slug(name: str) -> str:
    slug = _SLUG_KEEP.sub("-", name.lower()).strip("-._")
    return slug or "project"


def project_key(root: Path | str) -> str:
    """Readable, collision-resistant directory name for a project root."""
    resolved = Path(root).expanduser().resolve()
    digest = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[: _HASH_LENGTH]
    return f"{_slug(resolved.name)}-{digest}"


def project_dir(
    cwd: Path | str | None = None, *, root: Path | str | None = None
) -> Path:
    """``~/.zenagent/projects/<key>`` for ``root`` (or discovered from cwd)."""
    base = Path(root) if root is not None else find_project_root(cwd)
    return projects_root() / project_key(base)


def project_sessions_dir(
    cwd: Path | str | None = None, *, root: Path | str | None = None
) -> Path:
    """Per-project transcript directory (not created here)."""
    return project_dir(cwd, root=root) / "sessions"


def project_backups_dir(cwd: Path | str | None = None) -> Path:
    """Per-project write-snapshot store (not created here)."""
    return project_dir(cwd) / "backups"


def project_config_path(cwd: Path | str | None = None) -> Path:
    """Committable project config override: ``<root>/.zenagent/config.toml``."""
    return find_project_root(cwd) / ".zenagent" / "config.toml"


def project_mcp_path(cwd: Path | str | None = None) -> Path:
    """Committable project MCP definitions: ``<root>/.zenagent/mcp.json``."""
    return find_project_root(cwd) / ".zenagent" / "mcp.json"


@dataclass
class ProjectMeta:
    """Small, human-readable record stored next to a project's sessions."""

    root: str
    name: str
    last_used: str


@dataclass
class ProjectRelocation:
    """Outcome of re-pointing a project's state at a renamed folder."""

    old_root: str
    new_root: str
    old_key: str
    new_key: str
    sessions: int
    moved: bool


def _meta_path(root: Path | str | None = None, cwd: Path | str | None = None) -> Path:
    return project_dir(cwd, root=root) / "meta.json"


def _read_meta(target: Path) -> ProjectMeta | None:
    """Parse one ``meta.json``; missing/corrupt metadata means "unknown"."""
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    root_value = data.get("root")
    name_value = data.get("name")
    if not isinstance(root_value, str) or not root_value:
        return None
    return ProjectMeta(
        root=root_value,
        name=name_value if isinstance(name_value, str) else Path(root_value).name,
        last_used=str(data.get("last_used") or ""),
    )


def load_project_meta(
    cwd: Path | str | None = None, *, root: Path | str | None = None
) -> ProjectMeta | None:
    """Read ``meta.json``; missing/corrupt metadata means "unknown"."""
    return _read_meta(_meta_path(root=root, cwd=cwd))


def project_root_for_sessions_dir(session_dir: Path | str) -> str:
    """Canonical root recorded beside a sessions directory (``""`` when unknown).

    ``meta.json`` sits in the project state directory, next to ``sessions/``;
    the legacy flat directory has none, which reads as "root not recorded".
    """
    meta = _read_meta(Path(session_dir).parent / "meta.json")
    return meta.root if meta is not None else ""


def _write_meta(meta: ProjectMeta, target: Path) -> None:
    """Publish one ``meta.json`` atomically (private temp sibling + replace)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        dir=target.parent, prefix=f"{target.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(
                {"root": meta.root, "name": meta.name, "last_used": meta.last_used},
                handle,
                ensure_ascii=False,
                indent=2,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def write_project_meta(
    cwd: Path | str | None = None, *, root: Path | str | None = None
) -> ProjectMeta:
    """Create/refresh ``meta.json`` for a project (atomic replace)."""
    base = Path(root) if root is not None else find_project_root(cwd)
    base = base.expanduser().resolve()
    meta = ProjectMeta(
        root=str(base),
        name=base.name or "project",
        last_used=datetime.now(UTC).isoformat(timespec="seconds"),
    )
    _write_meta(meta, _meta_path(root=base))
    return meta


def relocate_project(
    old_root: Path | str, new_root: Path | str
) -> ProjectRelocation:
    """Move a project's state to the key of ``new_root`` (a renamed folder).

    Only the ``~/.zenagent/projects/<key>`` directory moves; transcripts are
    byte-identical because their project path comes from ``meta.json``, not
    from the records. Same-key targets are a no-op, and an existing target
    state directory is refused rather than merged. The caller must make sure
    no live runtime still owns the old state directory.
    """
    old_base = Path(old_root).expanduser().resolve()
    old_key = project_key(old_base)
    old_dir = projects_root() / old_key
    if not old_dir.is_dir():
        raise ValueError(f"no project state for {old_base}")
    target = Path(new_root).expanduser()
    if not target.is_dir():
        raise ValueError(f"not a directory: {target}")
    new_base = find_project_root(target)
    new_key = project_key(new_base)
    sessions = len(list((old_dir / "sessions").glob("*.jsonl")))
    if new_key == old_key:
        return ProjectRelocation(
            old_root=str(old_base),
            new_root=str(new_base),
            old_key=old_key,
            new_key=new_key,
            sessions=sessions,
            moved=False,
        )
    new_dir = projects_root() / new_key
    if new_dir.exists():
        raise ValueError(f"a project already uses {new_base}")
    previous = _read_meta(old_dir / "meta.json")
    last_used = (
        previous.last_used
        if previous is not None and previous.last_used
        else datetime.now(UTC).isoformat(timespec="seconds")
    )
    os.replace(old_dir, new_dir)
    try:
        _write_meta(
            ProjectMeta(
                root=str(new_base),
                name=new_base.name or "project",
                last_used=last_used,
            ),
            new_dir / "meta.json",
        )
    except BaseException:
        os.replace(new_dir, old_dir)
        raise
    return ProjectRelocation(
        old_root=str(old_base),
        new_root=str(new_base),
        old_key=old_key,
        new_key=new_key,
        sessions=sessions,
        moved=True,
    )


def all_session_dirs() -> list[Path]:
    """Legacy flat directory plus every known project's sessions directory."""
    directories = [legacy_sessions_dir()]
    root = projects_root()
    if root.is_dir():
        directories.extend(sorted(root.glob("*/sessions")))
    return directories


def project_key_for_session(path: Path | str) -> str | None:
    """Key when ``path`` sits under a project's sessions dir, else ``None``."""
    target = Path(path).expanduser().resolve()
    try:
        relative = target.relative_to(projects_root())
    except ValueError:
        return None
    parts = relative.parts
    if len(parts) >= 3 and parts[1] == "sessions":
        return parts[0]
    return None
