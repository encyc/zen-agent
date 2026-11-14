"""Assemble plugin contributions into a Runtime.

``zenagent.plugins`` emits raw dicts and never imports the MCP layer; this
module is the one place that knows both sides. It validates every contributed
server with the same parser ``mcp.json`` uses, merges user servers over plugin
servers, and swaps a live manager when plugins are reconfigured.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from zenagent.i18n import t
from zenagent.mcp.config import (
    McpServerConfig,
    load_mcp_config,
    parse_server,
    withhold_untrusted_project_servers,
)
from zenagent.mcp.manager import McpManager
from zenagent.plugins import state as plugin_state
from zenagent.plugins.discovery import discover_plugins
from zenagent.plugins.state import credentials_for, is_enabled, resolve

if TYPE_CHECKING:
    from zenagent.app.runtime import Runtime, RuntimeOptions

__all__ = [
    "PluginAssembly",
    "assemble_plugins",
    "attach_plugins",
    "reconfigure",
    "start_mcp",
    "start_plugins",
]


@dataclass
class PluginAssembly:
    configs: dict[str, McpServerConfig]
    skill_roots: list[Path]
    extension_files: list[str]
    statuses: dict[str, str]
    warnings: list[str]


def extension_drift(path: str | Path, plugin_id: str) -> bool:
    """Whether a plugin's contributed extension no longer matches its install.

    ``False`` when nothing was pinned (pre-pinning installs) or the file is
    gone — discovery reports missing files its own way.
    """
    recorded = plugin_state.extension_hash(plugin_id)
    if recorded is None:
        return False
    try:
        current = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return False
    return current != recorded


def assemble_plugins(
    *, user_configs: dict[str, McpServerConfig] | None = None
) -> PluginAssembly:
    """Merge installed plugins with the user's own MCP servers."""
    manifests, warnings = discover_plugins()
    statuses: dict[str, str] = {}
    servers: dict[str, McpServerConfig] = {}
    skill_roots: list[Path] = []
    extension_files: list[str] = []

    for manifest in manifests:
        if not is_enabled(manifest.id):
            statuses[manifest.id] = "disabled"
            continue
        resolved = resolve(manifest, credentials_for(manifest.id))
        skill_roots.extend(resolved.skill_roots)
        if resolved.extension is not None:
            if extension_drift(resolved.extension, manifest.id):
                warnings.append(
                    f"{manifest.id}: extension changed since install — "
                    "reinstall it if this was not you"
                )
            extension_files.append(str(resolved.extension))
        if resolved.missing:
            statuses[manifest.id] = "needs_credentials"
            warnings.append(
                f"{manifest.id}: missing credentials {', '.join(resolved.missing)}"
            )
            continue
        statuses[manifest.id] = "ready"
        for name, raw in resolved.servers.items():
            try:
                servers[name] = parse_server(name, raw)
            except ValueError as exc:
                warnings.append(f"{name}: {exc}")

    merged = {**servers, **(user_configs or {})}
    return PluginAssembly(merged, skill_roots, extension_files, statuses, warnings)


def _load_runtime_mcp(
    runtime: Runtime, options: RuntimeOptions | None
) -> dict[str, McpServerConfig]:
    """The session's user-layer MCP configs, behind the project trust gate.

    Shared by attach and reconfigure. An explicit ``--mcp-file`` is the
    user's own deliberate choice; the project layer is a committed file
    anyone can have written (audit P1-1), so its stdio servers need a
    per-name trust — on EVERY load: a plugin reload must not silently
    respawn the servers bootstrap withheld.
    """
    mcp_file = getattr(options, "mcp_file", None)
    configs = load_mcp_config(mcp_file, cwd=runtime.root)
    if options is not None and options.mcp_servers:
        configs = {
            name: config
            for name, config in configs.items()
            if name in options.mcp_servers
        }
    if mcp_file is None:
        configs, untrusted = withhold_untrusted_project_servers(
            configs, cwd=runtime.root
        )
        for name in untrusted:
            runtime._note(
                f"mcp: project server '{name}' is not trusted; "
                f"run: zen mcp trust {name}"
            )
    return configs


def attach_plugins(runtime: Runtime, options: RuntimeOptions) -> PluginAssembly:
    """Bootstrap step: assemble plugins and hand the result to the runtime."""
    user_configs: dict[str, McpServerConfig] = {}
    if runtime.profile.mcp:
        user_configs = _load_runtime_mcp(runtime, options)
    assembly = assemble_plugins(user_configs=user_configs)
    runtime.plugin_statuses = assembly.statuses
    runtime._plugin_skill_roots = assembly.skill_roots
    runtime._plugin_extension_files = assembly.extension_files
    for warning in assembly.warnings:
        runtime._note(f"plugins {warning}")
    return assembly


async def start_mcp(
    configs: dict[str, McpServerConfig],
    *,
    retry_interval_s: float,
    runtime: Runtime | None = None,
) -> McpManager | None:
    if not configs:
        return None
    manager = McpManager(configs, retry_interval_s=retry_interval_s)
    warnings = await manager.start()
    if runtime is not None:
        runtime.warnings.extend(f"mcp {warning}" for warning in warnings)
    return manager


async def start_plugins(runtime: Runtime, options: RuntimeOptions) -> PluginAssembly:
    """Bootstrap step: assemble plugins and start their MCP manager."""
    assembly = attach_plugins(runtime, options)
    if runtime.profile.mcp:
        runtime.mcp = await start_mcp(
            assembly.configs,
            retry_interval_s=options.mcp_retry_interval_s,
            runtime=runtime,
        )
    return assembly


async def reconfigure(runtime: Runtime) -> list[str]:
    """Re-apply plugin contributions: swap the manager, then rediscover.

    The new manager starts before the old one closes, so tool calls keep
    working across the swap. Streaming is refused up front: tools cannot be
    exchanged under an in-flight request.
    """
    agent = getattr(runtime, "agent", None)
    if agent is not None and agent.is_streaming:
        raise RuntimeError(t("cmdmsg.cannot_reload"))
    options = runtime._options
    user_configs: dict[str, McpServerConfig] = {}
    if runtime.profile.mcp:
        # Same trust gate as bootstrap: a reload must not respawn the
        # untrusted project servers that were withheld at start.
        user_configs = _load_runtime_mcp(runtime, options)
    assembly = assemble_plugins(user_configs=user_configs)
    old, runtime.mcp = runtime.mcp, None
    runtime.plugin_statuses = assembly.statuses
    runtime._plugin_skill_roots = assembly.skill_roots
    runtime._plugin_extension_files = assembly.extension_files
    runtime.extension_files = [
        *runtime.user_config.extensions,
        *(options.extension_paths if options is not None else []),
        *assembly.extension_files,
    ]
    if runtime.profile.mcp:
        retry = getattr(options, "mcp_retry_interval_s", None) or 30.0
        runtime.mcp = await start_mcp(
            assembly.configs, retry_interval_s=retry, runtime=runtime
        )
    if old is not None:
        await old.close()
    for warning in assembly.warnings:
        runtime._note(f"plugins {warning}")
    runtime.rediscover()
    ready = sum(1 for status in runtime.plugin_statuses.values() if status == "ready")
    return [f"{len(runtime.plugin_statuses)} plugins ({ready} ready)"]
