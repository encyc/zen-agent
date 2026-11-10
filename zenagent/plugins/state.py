"""Plugin state: on/off switches, namespaced credentials, contribution resolution.

Credentials live in ``settings.json`` under ``plugin:<id>:<KEY>`` so a manifest,
a project file, or the environment can never supply one: only the store's
explicit value counts. Resolution fills a manifest's ``${KEY}`` placeholders
and returns plain dicts the assembly layer hands to the existing MCP manager —
the plugin still never calls back into the host.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from zenagent import settings
from zenagent.plugins.manifest import PluginManifest

__all__ = [
    "PLUGIN_STATE_PREF",
    "ResolvedContributions",
    "clear_plugin",
    "credential_name",
    "credentials_for",
    "is_enabled",
    "resolve",
    "set_credentials",
    "set_enabled",
]

PLUGIN_STATE_PREF = "plugins"

_PLACEHOLDER = re.compile(r"\$\{([A-Z0-9_]+)\}")


def credential_name(plugin_id: str, key: str) -> str:
    return f"plugin:{plugin_id}:{key}"


def _states() -> dict:
    """``prefs["plugins"]``, or an empty mapping when hand-edited into junk."""
    value = settings.get_pref(PLUGIN_STATE_PREF, {})
    return value if isinstance(value, dict) else {}


def is_enabled(plugin_id: str) -> bool:
    entry = _states().get(plugin_id)
    if not isinstance(entry, dict):
        return True
    return bool(entry.get("enabled", True))


def set_enabled(plugin_id: str, enabled: bool) -> None:
    states = dict(_states())
    entry = states.get(plugin_id)
    entry = dict(entry) if isinstance(entry, dict) else {}
    entry["enabled"] = bool(enabled)
    states[plugin_id] = entry
    settings.set_pref(PLUGIN_STATE_PREF, states)


def credentials_for(plugin_id: str) -> dict[str, str]:
    """Stored plugin credentials only — never the environment fallback."""
    prefix = credential_name(plugin_id, "")
    found: dict[str, str] = {}
    for name, value in settings.load().api_keys.items():
        if name.startswith(prefix) and isinstance(value, str) and value:
            found[name[len(prefix) :]] = value
    return found


def set_credentials(plugin_id: str, values: dict[str, str]) -> None:
    store = settings.load()
    for key, value in values.items():
        name = credential_name(plugin_id, key)
        cleaned = value.strip()
        if cleaned:
            store.api_keys[name] = cleaned
        else:
            store.api_keys.pop(name, None)
    settings.save(store)


def clear_plugin(plugin_id: str) -> None:
    store = settings.load()
    states = store.prefs.get(PLUGIN_STATE_PREF)
    if isinstance(states, dict) and plugin_id in states:
        store.prefs[PLUGIN_STATE_PREF] = {
            name: value for name, value in states.items() if name != plugin_id
        }
    prefix = credential_name(plugin_id, "")
    for name in [name for name in store.api_keys if name.startswith(prefix)]:
        del store.api_keys[name]
    settings.save(store)


@dataclass
class ResolvedContributions:
    servers: dict[str, dict]
    skill_roots: list[Path]
    extension: Path | None
    missing: list[str]


def resolve(
    manifest: PluginManifest, credentials: dict[str, str]
) -> ResolvedContributions:
    missing = [
        spec.key for spec in manifest.credentials if not credentials.get(spec.key)
    ]

    skill_root = manifest.root / manifest.skills if manifest.skills else None
    skill_roots = [skill_root] if skill_root is not None and skill_root.is_dir() else []

    extension = manifest.root / manifest.extension if manifest.extension else None
    if extension is not None and not extension.exists():
        extension = None

    if missing:
        return ResolvedContributions({}, skill_roots, extension, missing)

    servers = {
        f"{manifest.id}.{key}": _fill_server(raw, credentials)
        for key, raw in manifest.mcp_servers.items()
    }
    return ResolvedContributions(servers, skill_roots, extension, [])


def _fill(value: str, credentials: dict[str, str]) -> str:
    return _PLACEHOLDER.sub(lambda match: credentials.get(match.group(1), ""), value)


def _fill_server(raw: dict, credentials: dict[str, str]) -> dict:
    server = dict(raw)
    command = server.get("command")
    if isinstance(command, list):
        server["command"] = [
            _fill(part, credentials) if isinstance(part, str) else part
            for part in command
        ]
    for field_name in ("env", "headers"):
        mapping = server.get(field_name)
        if isinstance(mapping, dict):
            server[field_name] = {
                name: _fill(value, credentials) if isinstance(value, str) else value
                for name, value in mapping.items()
            }
    url = server.get("url")
    if isinstance(url, str):
        server["url"] = _fill(url, credentials)
    return server
