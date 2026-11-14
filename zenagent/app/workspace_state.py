"""The backend source of truth for a project's workspace mode.

The desktop used to own ``workspaceMode`` in localStorage; the backend only
inferred a mode from whether ``zen_fleet`` or a swarm room existed. That made
the mode a UI opinion. This module stores the desired mode as project state:

    ~/.zenagent/projects/<key>/workspace.json

It is deliberately small and side-effect free. Starting/stopping the zen fleet
or opening a swarm room remains the orchestrators' job; ``workspace.set`` only
records which mode the project is in so every frontend can read the same fact.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from zenagent import project
from zenagent.app.support import atomic_write_text

__all__ = [
    "WORKSPACE_MODES",
    "WorkspaceState",
    "load_workspace",
    "save_workspace",
    "set_workspace_mode",
    "workspace_path",
]

WORKSPACE_MODES = ("coding", "zen", "swarm")


@dataclass(frozen=True)
class WorkspaceState:
    mode: str = "coding"
    updated_at: int = 0
    router_session: str | None = None

    def to_json(self) -> dict:
        return {
            "mode": self.mode,
            "updatedAt": self.updated_at,
            "routerSession": self.router_session,
        }


def workspace_path(root: Path | str) -> Path:
    return project.project_dir(root=Path(root)) / "workspace.json"


def load_workspace(root: Path | str) -> WorkspaceState:
    """Read the mode; a missing/corrupt file falls back to ``coding``."""
    path = workspace_path(root)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return WorkspaceState()
    if not isinstance(raw, dict):
        return WorkspaceState()
    mode = str(raw.get("mode") or "coding")
    if mode not in WORKSPACE_MODES:
        mode = "coding"
    try:
        updated_at = int(raw.get("updatedAt") or 0)
    except (TypeError, ValueError):
        updated_at = 0
    router = raw.get("routerSession")
    return WorkspaceState(
        mode=mode,
        updated_at=updated_at,
        router_session=str(router) if isinstance(router, str) and router else None,
    )


def save_workspace(root: Path | str, state: WorkspaceState) -> WorkspaceState:
    """Atomically persist one state and stamp its update time."""
    current = WorkspaceState(
        mode=state.mode,
        updated_at=state.updated_at or int(time.time()),
        router_session=state.router_session,
    )
    path = workspace_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        path, json.dumps(current.to_json(), ensure_ascii=False, indent=2) + "\n"
    )
    return current


def set_workspace_mode(
    root: Path | str,
    mode: str,
    *,
    router_session: str | None = None,
) -> WorkspaceState:
    if mode not in WORKSPACE_MODES:
        raise ValueError(f"unknown workspace mode: {mode}")
    return save_workspace(
        root,
        WorkspaceState(
            mode=mode,
            updated_at=int(time.time()),
            router_session=router_session,
        ),
    )
