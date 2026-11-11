"""Role files: the on-disk contract between the manager and Settings.

``write_role`` validates the id, moves ``systemPrompt`` into prompt.md, writes
manifest.json, and reparses the directory so the caller sees exactly what a
restart would load. ``role_view`` is the RPC-facing projection; it carries the
names of missing credentials, never their values.
"""

from __future__ import annotations

import contextlib
import copy
import json
import re
import shutil
from pathlib import Path

from zenagent.app.swarm.roles import (
    RoleManifest,
    agents_root,
    parse_role,
    project_agents_root,
    workspaces_root,
)

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


def _read_manifest(path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def write_role(role_id: str, manifest: dict, *, root) -> RoleManifest:
    if not _ID_RE.fullmatch(role_id):
        raise ValueError(f"invalid role id {role_id!r}")
    payload = dict(manifest)
    prompt = payload.pop("systemPrompt", None)
    payload["id"] = role_id
    role_dir = project_agents_root(root) / role_id
    role_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = role_dir / "manifest.json"
    # Settings sends only the fields it shows; everything else in the file
    # (skills, plugins, MCP servers, inheritProject) must survive an edit.
    merged = _read_manifest(manifest_path)
    for key, value in payload.items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    manifest_path.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if prompt is not None:
        (role_dir / "prompt.md").write_text(str(prompt), encoding="utf-8")
    role, warnings = parse_role(role_dir, project_root=root)
    if role is None:
        raise ValueError("; ".join(warnings) or f"invalid role {role_id}")
    return role


def _project_copy_manifest(source, role_id: str) -> dict | None:
    """Strip defaults the old global editor wrote into the library manifest.

    The pre-project Settings form read the *computed* library root and always
    saved `inheritProject`, so those values are indistinguishable from user
    choices unless they still equal the library defaults. Copying them
    verbatim would point every project's copy at one shared workspace with no
    project context; only the materialised defaults are dropped, so a pinned
    root or isolation choice survives.
    """
    raw = _read_manifest(source / "manifest.json")
    root = raw.get("root")
    if not isinstance(root, str) or not root.strip():
        return None
    try:
        pinned = Path(root).expanduser().resolve()
    except (OSError, RuntimeError):
        return None
    if pinned != (workspaces_root() / role_id).resolve():
        return None
    raw.pop("root", None)
    if raw.get("inheritProject") is False:
        raw.pop("inheritProject", None)
    return raw


def copy_role(role_id: str, *, root) -> RoleManifest:
    """Copy a library role into the project, leaving the library untouched.

    Bytes are copied verbatim: ``${KEY}`` credential placeholders stay
    unresolved on disk, exactly as they are stored in the library. Only the
    library's materialised defaults are removed (see ``_project_copy_manifest``).
    """
    if not _ID_RE.fullmatch(role_id):
        raise ValueError(f"invalid role id {role_id!r}")
    source = agents_root() / role_id
    if not source.is_dir():
        raise ValueError(f"no library role {role_id}")
    target = project_agents_root(root) / role_id
    if target.exists():
        raise ValueError(f"role {role_id} already exists in this project")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)
    manifest = _project_copy_manifest(source, role_id)
    if manifest is not None:
        (target / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    role, warnings = parse_role(target, project_root=root)
    if role is None:
        raise ValueError("; ".join(warnings) or f"invalid role {role_id}")
    return role


def remove_role_dir(role_id: str, *, root) -> None:
    base = project_agents_root(root)
    role_dir = base / role_id
    if role_dir.parent != base:
        return
    with contextlib.suppress(OSError):
        shutil.rmtree(role_dir)


def role_signature(role: RoleManifest) -> tuple:
    return (
        role.name,
        role.description,
        role.model,
        role.thinking,
        role.system_prompt,
        tuple(role.skills),
        tuple(role.plugins),
        # Resolved servers flatten credentials, so compare the raw manifest
        # entries too: editing a command or env must rebuild the runtime.
        tuple(
            (name, json.dumps(entry, sort_keys=True, default=str))
            for name, entry in sorted(role.mcp_manifest.items())
        ),
        tuple(str(path) for path in role.skill_roots),
        tuple(sorted(role.mcp_servers)),
        tuple(role.tools or ()),
        str(role.root),
        role.inherit_project,
        role.max_turns,
        role.max_cost,
        role.mode,
    )


def role_view(role: RoleManifest) -> dict:
    return {
        "id": role.id,
        "name": role.name,
        "description": role.description,
        "model": role.model,
        "thinking": role.thinking,
        "systemPrompt": role.system_prompt,
        "skills": list(role.skills),
        "plugins": list(role.plugins),
        "mcpServers": copy.deepcopy(role.mcp_manifest),
        "mode": role.mode,
        "root": str(role.root),
        "tools": list(role.tools) if role.tools is not None else None,
        "inheritProject": role.inherit_project,
        "maxTurns": role.max_turns,
        "maxCost": role.max_cost,
        "missingCredentials": list(role.missing_credentials),
    }
