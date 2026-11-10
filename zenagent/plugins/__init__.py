"""Declarative capability packs: a directory plus ``manifest.json``.

The package is a base-cluster extension surface. It discovers, validates and
stores plugins, and emits raw contribution dicts; wiring them into the MCP
client, skills and extensions is the app layer's job. That keeps this package
free of imports from ``mcp/``, ``app/`` and ``cli/``.
"""

from __future__ import annotations

from zenagent.plugins.discovery import discover_plugins, installed_root
from zenagent.plugins.install import install_plugin, uninstall_plugin
from zenagent.plugins.manifest import CredentialSpec, PluginManifest, parse_manifest
from zenagent.plugins.state import (
    PLUGIN_STATE_PREF,
    ResolvedContributions,
    clear_plugin,
    credential_name,
    credentials_for,
    is_enabled,
    resolve,
    set_credentials,
    set_enabled,
)

__all__ = [
    "PLUGIN_STATE_PREF",
    "CredentialSpec",
    "PluginManifest",
    "ResolvedContributions",
    "clear_plugin",
    "credential_name",
    "credentials_for",
    "discover_plugins",
    "install_plugin",
    "installed_root",
    "is_enabled",
    "parse_manifest",
    "resolve",
    "set_credentials",
    "set_enabled",
    "uninstall_plugin",
]
