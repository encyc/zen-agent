"""Builtin MCP servers: definitions shipped inside the package.

A builtin server is one whose *definition* belongs to zen rather than to the
user's ``mcp.json``: it needs no credentials, no per-project trust and no
install directory, and its version must move with the zen release that pinned
it. Computer Use (``agent-desktop``) is the first one.

Why here and not ``zenagent/plugins/data/``: a plugin is a distribution shell
for connectors that need credentials and bundled skills (``zen.dingtalk``). A
builtin server has neither, and a plugin's contributed server name would become
``<plugin id>.<key>`` — a name the MCP settings page does not even list.

The switch lives in ``settings.json`` (``prefs.builtin_mcp``), never in
``mcp.json``: a pin written into a user file cannot be moved by a zen upgrade,
which is the one property this layer exists to guarantee.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from zenagent import settings

if TYPE_CHECKING:
    from zenagent.mcp.config import McpServerConfig

__all__ = [
    "BUILTIN_SERVERS",
    "BUILTIN_STATE_PREF",
    "COMPUTE_USE_ID",
    "COMPUTE_USE_VERSION",
    "BuiltinServer",
    "builtin_configs",
    "builtin_row",
    "disable_builtin",
    "enable_builtin",
    "is_builtin_enabled",
]

#: ``settings.json`` → ``prefs["builtin_mcp"]``: ``{name: {"enabled": bool}}``.
BUILTIN_STATE_PREF = "builtin_mcp"

#: The agent-desktop release zen ships against. Bumping this is the whole
#: versioning story: one constant, retested against a live handshake before the
#: zen release it rides in (see docs/guide/mcp.md, "内置 server").
COMPUTE_USE_VERSION = "1.3.1"

#: The id of the Computer Use server (stable: it names tools and preferences).
COMPUTE_USE_ID = "compute-use"


@dataclass(frozen=True)
class BuiltinServer:
    """One builtin MCP server definition."""

    id: str
    label: str
    description: str
    command: tuple[str, ...]
    version: str
    requires_runtime: str = ""
    #: The agent-visible proxy tool this server is folded into. When set, the
    #: manager's raw tools stay hidden unless the user asks for them.
    proxy_tool: str | None = None

    def config(self) -> McpServerConfig:
        # Imported here: ``config`` imports this module for its lowest layer,
        # so a module-level import would close the cycle.
        from zenagent.mcp.config import McpServerConfig as _Config

        return _Config(name=self.id, command=list(self.command))


BUILTIN_SERVERS: dict[str, BuiltinServer] = {
    COMPUTE_USE_ID: BuiltinServer(
        id=COMPUTE_USE_ID,
        label="Computer Use",
        description=(
            "通过系统无障碍树看见并操作本机应用（macOS / Windows / Linux）。"
        ),
        command=("npx", "-y", f"agent-desktop@{COMPUTE_USE_VERSION}", "mcp"),
        version=COMPUTE_USE_VERSION,
        requires_runtime="Node.js 18+ (npx)",
        proxy_tool="desktop",
    ),
}


def _states() -> dict:
    """``prefs["builtin_mcp"]``, or an empty mapping when hand-edited into junk."""
    value = settings.get_pref(BUILTIN_STATE_PREF, {})
    return value if isinstance(value, dict) else {}


def is_builtin_enabled(name: str) -> bool:
    """Whether the user switched this builtin server on.

    Builtin servers are opt-in: an unknown or absent entry means off, so a new
    zen release never starts spawning a 32MB npm package behind the user's back.
    """
    entry = _states().get(name)
    if not isinstance(entry, dict):
        return False
    return bool(entry.get("enabled", False))


def set_builtin_enabled(name: str, enabled: bool) -> None:
    """Write one switch (the only thing an "install" ever persists)."""
    if name not in BUILTIN_SERVERS:
        raise ValueError(f"unknown builtin MCP server {name!r}")
    states = dict(_states())
    entry = states.get(name)
    entry = dict(entry) if isinstance(entry, dict) else {}
    entry["enabled"] = bool(enabled)
    states[name] = entry
    settings.set_pref(BUILTIN_STATE_PREF, states)


def enable_builtin(name: str) -> None:
    set_builtin_enabled(name, True)


def disable_builtin(name: str) -> None:
    set_builtin_enabled(name, False)


def builtin_configs() -> dict[str, McpServerConfig]:
    """Configs for every enabled builtin server (empty by default)."""
    return {
        name: server.config()
        for name, server in BUILTIN_SERVERS.items()
        if is_builtin_enabled(name)
    }


def builtin_row(name: str) -> dict:
    """One settings-page row; never carries a resolved command line."""
    server = BUILTIN_SERVERS[name]
    return {
        "id": server.id,
        "label": server.label,
        "description": server.description,
        "version": server.version,
        "requires_runtime": server.requires_runtime,
        "proxy_tool": server.proxy_tool,
        "enabled": is_builtin_enabled(name),
    }
