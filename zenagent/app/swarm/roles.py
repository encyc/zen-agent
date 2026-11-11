"""Declarative swarm roles: manifests, discovery and credential filling.

A role is a directory under ``~/.zenagent/agents/<id>/`` holding
``manifest.json`` and a persona prompt. Parsing never raises: a broken role is
skipped with a warning so one bad manifest cannot take the room down.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from zenagent import project
from zenagent.app.modes import DEFAULT_MODE, MODES
from zenagent.mcp.config import parse_server
from zenagent.plugins.discovery import discover_plugins
from zenagent.plugins.state import credentials_for, is_enabled, resolve
from zenagent.settings import get_api_key

__all__ = [
    "RoleManifest",
    "agents_root",
    "discover_roles",
    "parse_role",
    "role_session_path",
    "state_root",
    "workspaces_root",
]

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_PLACEHOLDER = re.compile(r"\$\{([A-Za-z0-9_]+)\}")
DEFAULT_PROMPT_FILE = "prompt.md"


@dataclass
class RoleManifest:
    id: str
    name: str
    description: str = ""
    model: str | None = None
    thinking: str | None = None
    system_prompt: str = ""
    # Raw manifest entries, kept for the Settings editor. `skill_roots` and
    # `mcp_servers` are resolved (and the latter holds credentials), so the
    # editor reads these instead and only ever echoes placeholders.
    skills: list[str] = field(default_factory=list)
    plugins: list[str] = field(default_factory=list)
    mcp_manifest: dict[str, Any] = field(default_factory=dict)
    skill_roots: list[Path] = field(default_factory=list)
    mcp_servers: dict[str, dict] = field(default_factory=dict)
    tools: list[str] | None = None
    root: Path = field(default_factory=Path)
    inherit_project: bool = False
    max_turns: int | None = None
    max_cost: float | None = None
    mode: str = DEFAULT_MODE
    missing_credentials: list[str] = field(default_factory=list)


def agents_root() -> Path:
    return project.zenagent_home() / "agents"


def workspaces_root() -> Path:
    return Path.home() / ".zenagent-workspaces"


def state_root() -> Path:
    return project.zenagent_home() / "swarm"


def role_session_path(role_id: str, *, fresh: bool = False) -> Path:
    """The role's session file: the newest existing, or a fresh empty one.

    Kept out of the project session dirs on purpose (design §7): role
    transcripts never appear in the normal session list, which is why the role
    runtime sets ``project_sessions=False``.
    """
    directory = state_root() / "agents" / role_id / "sessions"
    directory.mkdir(parents=True, exist_ok=True)
    if not fresh:
        latest = _latest_session(directory)
        if latest is not None:
            return latest
    stamp = time.strftime("%Y%m%dT%H%M%S", time.localtime())
    path = directory / f"{stamp}-{time.time_ns() % 1_000_000_000}.jsonl"
    path.touch()
    return path


def _latest_session(directory: Path) -> Path | None:
    files = []
    for path in directory.glob("*.jsonl"):
        try:
            files.append((path.stat().st_mtime, path))
        except OSError:
            continue
    if not files:
        return None
    return max(files)[1]


def _within(role_dir: Path, candidate: Path) -> bool:
    return candidate.resolve().is_relative_to(role_dir.resolve())


def parse_role(role_dir: Path) -> tuple[RoleManifest | None, list[str]]:
    role_dir = Path(role_dir)
    warnings: list[str] = []
    try:
        raw = json.loads((role_dir / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, [f"role {role_dir.name}: {exc}"]
    if not isinstance(raw, dict):
        return None, [f"role {role_dir.name}: manifest must be an object"]

    role_id = raw.get("id") or role_dir.name
    if not isinstance(role_id, str) or not _ID_RE.fullmatch(role_id):
        return None, [f"role {role_dir.name}: invalid id {role_id!r}"]

    prompt_file = raw.get("systemPromptFile") or DEFAULT_PROMPT_FILE
    prompt_path = role_dir / str(prompt_file)
    if not _within(role_dir, prompt_path):
        system_prompt = ""
        warnings.append(f"role {role_id}: prompt {prompt_file!r} escapes the role directory")
    else:
        try:
            system_prompt = prompt_path.read_text(encoding="utf-8")
        except OSError as exc:
            system_prompt = ""
            warnings.append(f"role {role_id}: cannot read prompt {prompt_file}: {exc}")

    skill_entries = _str_list(raw.get("skills"))
    skill_roots: list[Path] = []
    for entry in skill_entries:
        path = role_dir / entry
        if not _within(role_dir, path):
            warnings.append(f"role {role_id}: skills {entry!r} escapes the role directory")
            continue
        if path.is_dir():
            skill_roots.append(path)
        else:
            warnings.append(f"role {role_id}: missing skills dir {entry!r}")

    mcp_servers: dict[str, dict] = {}
    missing_credentials: list[str] = []
    raw_servers = raw.get("mcpServers")
    if raw_servers is not None and not isinstance(raw_servers, dict):
        warnings.append(f"role {role_id}: mcpServers must be an object")
        raw_servers = {}
    mcp_manifest = {str(name): entry for name, entry in (raw_servers or {}).items()}
    for name, entry in mcp_manifest.items():
        missing: list[str] = []
        filled = _fill(entry, missing)
        if missing:
            missing_credentials.extend(missing)
            warnings.append(
                f"role {role_id}: mcp server {name} missing {', '.join(_dedupe(missing))}"
            )
            continue
        try:
            parse_server(str(name), filled)
        except ValueError as exc:
            warnings.append(f"role {role_id}: mcp server {name}: {exc}")
            continue
        mcp_servers[str(name)] = filled

    plugins = _str_list(raw.get("plugins"))
    loaded_plugins = _load_plugins(role_id, plugins, warnings)
    for manifest, contributions in loaded_plugins:
        skill_roots.extend(contributions.skill_roots)
        for name, entry in contributions.servers.items():
            if name in mcp_servers:
                continue
            try:
                parse_server(name, entry)
            except ValueError as exc:
                warnings.append(f"role {role_id}: plugin server {name}: {exc}")
                continue
            mcp_servers[name] = entry

    explicit_root = raw.get("root")
    if explicit_root:
        root = Path(str(explicit_root)).expanduser().resolve()
    else:
        root = (workspaces_root() / role_id).resolve()
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        warnings.append(f"role {role_id}: cannot create workspace {root}: {exc}")

    limits = raw.get("limits")
    if not isinstance(limits, dict):
        limits = {}

    mode = str(raw.get("mode") or DEFAULT_MODE)
    if mode not in MODES:
        warnings.append(f"role {role_id}: unknown mode {mode!r}; using {DEFAULT_MODE}")
        mode = DEFAULT_MODE

    tools_raw = raw.get("tools")
    tools = [str(item) for item in tools_raw] if isinstance(tools_raw, list) else None

    role = RoleManifest(
        id=role_id,
        name=str(raw.get("name") or role_id),
        description=str(raw.get("description") or ""),
        model=str(raw["model"]) if raw.get("model") else None,
        thinking=str(raw["thinking"]) if raw.get("thinking") else None,
        system_prompt=system_prompt,
        skills=skill_entries,
        plugins=plugins,
        mcp_manifest=mcp_manifest,
        skill_roots=skill_roots,
        mcp_servers=mcp_servers,
        tools=tools,
        root=root,
        inherit_project=bool(raw.get("inheritProject", False)),
        max_turns=_as_int(limits.get("maxTurns")),
        max_cost=_as_float(limits.get("maxCost")),
        mode=mode,
        missing_credentials=_dedupe(missing_credentials),
    )
    return role, warnings


def discover_roles() -> tuple[list[RoleManifest], list[str]]:
    base = agents_root()
    roles: list[RoleManifest] = []
    warnings: list[str] = []
    if not base.is_dir():
        return roles, warnings
    for entry in sorted(base.iterdir()):
        if entry.name.startswith(".") or not entry.is_dir():
            continue
        if not (entry / "manifest.json").is_file():
            continue
        role, role_warnings = parse_role(entry)
        warnings.extend(role_warnings)
        if role is not None:
            roles.append(role)
    roles.sort(key=lambda role: role.id)
    return roles, warnings


def _load_plugins(role_id: str, plugin_ids: list[str], warnings: list[str]) -> list:
    if not plugin_ids:
        return []
    installed, discovery_warnings = discover_plugins()
    warnings.extend(discovery_warnings)
    by_id = {manifest.id: manifest for manifest in installed}
    resolved = []
    for plugin_id in plugin_ids:
        manifest = by_id.get(plugin_id)
        if manifest is None:
            warnings.append(f"role {role_id}: unknown plugin {plugin_id!r}")
            continue
        if not is_enabled(plugin_id):
            warnings.append(f"role {role_id}: plugin {plugin_id!r} is disabled")
            continue
        contributions = resolve(manifest, credentials_for(plugin_id))
        if contributions.missing:
            warnings.append(
                f"role {role_id}: plugin {plugin_id!r} needs credentials: "
                f"{', '.join(contributions.missing)}"
            )
            continue
        resolved.append((manifest, contributions))
    return resolved


def _fill(value: Any, missing: list[str]) -> Any:
    if isinstance(value, str):

        def replace(match: re.Match[str]) -> str:
            key = match.group(1)
            resolved = get_api_key(key)
            if not resolved:
                missing.append(key)
                return match.group(0)
            return resolved

        return _PLACEHOLDER.sub(replace, value)
    if isinstance(value, list):
        return [_fill(item, missing) for item in value]
    if isinstance(value, dict):
        return {key: _fill(item, missing) for key, item in value.items()}
    return value


def _str_list(value: Any) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


def _as_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))
