"""Role files: the on-disk contract between the manager and Settings.

``write_role`` validates the id, moves ``systemPrompt`` into prompt.md, writes
manifest.json, and reparses the directory so the caller sees exactly what a
restart would load. ``role_view`` is the RPC-facing projection; it carries the
names of missing credentials, never their values.
"""

from __future__ import annotations

import contextlib
import json
import re
import shutil

from zenagent.app.swarm.roles import (
    RoleManifest,
    agents_root,
    parse_role,
)

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


def _read_manifest(path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def write_role(role_id: str, manifest: dict) -> RoleManifest:
    if not _ID_RE.fullmatch(role_id):
        raise ValueError(f"invalid role id {role_id!r}")
    payload = dict(manifest)
    prompt = payload.pop("systemPrompt", None)
    payload["id"] = role_id
    role_dir = agents_root() / role_id
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
    role, warnings = parse_role(role_dir)
    if role is None:
        raise ValueError("; ".join(warnings) or f"invalid role {role_id}")
    return role


def remove_role_dir(role_id: str) -> None:
    base = agents_root()
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
        "mode": role.mode,
        "root": str(role.root),
        "tools": list(role.tools) if role.tools is not None else None,
        "inheritProject": role.inherit_project,
        "maxTurns": role.max_turns,
        "maxCost": role.max_cost,
        "missingCredentials": list(role.missing_credentials),
    }
