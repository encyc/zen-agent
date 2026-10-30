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
    "all_session_dirs",
    "find_project_root",
    "legacy_sessions_dir",
    "load_project_meta",
    "project_config_path",
    "project_dir",
    "project_key",
    "project_key_for_session",
    "project_mcp_path",
    "project_sessions_dir",
    "projects_root",
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


def _meta_path(root: Path | str | None = None, cwd: Path | str | None = None) -> Path:
    return project_dir(cwd, root=root) / "meta.json"


def load_project_meta(
    cwd: Path | str | None = None, *, root: Path | str | None = None
) -> ProjectMeta | None:
    """Read ``meta.json``; missing/corrupt metadata means "unknown"."""
    target = _meta_path(root=root, cwd=cwd)
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
    target = _meta_path(root=base)
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
    return meta


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
