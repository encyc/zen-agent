"""MCP server configuration (``~/.zenagent/mcp.json`` plus a project layer).

JSON on purpose: the kernel keeps zero dependencies and the shape is small.

    {
      "servers": {
        "files": {"command": ["npx", "-y", "@modelcontextprotocol/server-filesystem", "/tmp"]},
        "remote": {"url": "https://example/mcp", "headers": {"authorization": "Bearer x"}}
      }
    }

By default the global file is layered with ``<root>/.zenagent/mcp.json`` (root =
nearest directory with ``.git``): servers are merged by name and the project
definition of a same-named server wins. The project file is meant to be
committed, so credentials belong in the global file or in the environment.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from zenagent import project
from zenagent.mcp.client import HttpTransport, StdioTransport, Transport
from zenagent.project import project_dir

__all__ = [
    "McpServerConfig",
    "config_path",
    "layer_path",
    "load_mcp_config",
    "load_mcp_layers",
    "make_transport",
    "parse_server",
    "remove_mcp_server",
    "save_mcp_server",
    "save_server",
]


@dataclass
class McpServerConfig:
    """One configured server: either a command (stdio) or a URL (HTTP)."""

    name: str
    command: list[str] | None = None
    env: dict[str, str] = field(default_factory=dict)
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def transport_kind(self) -> str:
        return "http" if self.url else "stdio"


def config_path() -> Path:
    return Path.home() / ".zenagent" / "mcp.json"


def _parse_server(name: str, raw: object) -> McpServerConfig:
    if not isinstance(raw, dict):
        raise ValueError(  # noqa: TRY004 - config errors are ValueError by contract
            f"mcp server {name!r} must be an object"
        )
    command = raw.get("command")
    url = raw.get("url")
    if command is None and url is None:
        raise ValueError(f"mcp server {name!r} needs either 'command' or 'url'")
    if command is not None and url is not None:
        raise ValueError(f"mcp server {name!r} cannot have both 'command' and 'url'")
    if command is not None and (
        not isinstance(command, list) or not all(isinstance(part, str) for part in command)
    ):
        raise ValueError(
            f"mcp server {name!r} command must be a list of strings"
        )
    env = raw.get("env") or {}
    headers = raw.get("headers") or {}
    for label, value in (("env", env), ("headers", headers)):
        if not isinstance(value, dict) or not all(
            isinstance(key, str) and isinstance(item, str) for key, item in value.items()
        ):
            raise ValueError(
                f"mcp server {name!r} {label} must map strings to strings"
            )
    return McpServerConfig(
        name=name,
        command=list(command) if command is not None else None,
        env=dict(env),
        url=str(url) if url is not None else None,
        headers=dict(headers),
    )


def _load_mcp_file(target: Path) -> dict[str, McpServerConfig]:
    """Load one mcp.json; a missing file simply means "no servers"."""
    if not target.is_file():
        return {}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"cannot read mcp config {target}: {exc}") from exc
    servers = data.get("servers") if isinstance(data, dict) else None
    if servers is None:
        return {}
    if not isinstance(servers, dict):
        raise ValueError(  # noqa: TRY004 - config errors are ValueError by contract
            f"mcp config {target}: 'servers' must be an object"
        )
    return {name: _parse_server(name, raw) for name, raw in servers.items()}


def load_mcp_config(
    path: Path | None = None, *, cwd: Path | None = None
) -> dict[str, McpServerConfig]:
    """Load configured servers, global layer first, then the project layer.

    An explicit ``path`` disables layering and reads just that file. Otherwise
    the project file's same-named server replaces the global definition and any
    other global servers remain.
    """
    if path is not None:
        return _load_mcp_file(Path(path))
    servers = _load_mcp_file(config_path())
    servers.update(_load_mcp_file(project.project_mcp_path(cwd)))
    return servers


def layer_path(layer: str, *, cwd: Path | None = None) -> Path:
    """The file a management write targets (``global`` or ``project``)."""
    if layer == "global":
        return config_path()
    if layer == "project":
        return project.project_mcp_path(cwd)
    raise ValueError(f"unknown MCP layer {layer!r}")


def load_mcp_layers(
    *, cwd: Path | None = None
) -> tuple[dict[str, McpServerConfig], dict[str, McpServerConfig]]:
    """Read the global and project layers separately (for a management view)."""
    return (
        _load_mcp_file(config_path()),
        _load_mcp_file(project.project_mcp_path(cwd)),
    )


def _read_layer(target: Path) -> dict:
    """The raw document for editing; a corrupt file is never overwritten."""
    if not target.is_file():
        return {"servers": {}}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"cannot read mcp config {target}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(  # noqa: TRY004 - config errors are ValueError by contract
            f"mcp config {target} must contain an object"
        )
    servers = data.get("servers")
    if servers is None:
        data["servers"] = {}
    elif not isinstance(servers, dict):
        raise ValueError(
            f"mcp config {target}: 'servers' must be an object"
        )
    return data


def _write_layer(target: Path, data: dict) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        dir=target.parent, prefix=f"{target.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, target)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def _server_payload(config: McpServerConfig) -> dict:
    payload: dict = (
        {"command": list(config.command)}
        if config.command is not None
        else {"url": config.url}
    )
    if config.env:
        payload["env"] = dict(config.env)
    if config.headers:
        payload["headers"] = dict(config.headers)
    return payload


def save_mcp_server(
    name: str,
    *,
    command: list[str] | None = None,
    url: str | None = None,
    env: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    layer: str = "global",
    cwd: Path | None = None,
) -> Path:
    """Add/replace one server in the chosen layer (atomic, mode 0600)."""
    raw: dict = {}
    if command is not None:
        raw["command"] = list(command)
    if url is not None:
        raw["url"] = url
    if env:
        raw["env"] = dict(env)
    if headers:
        raw["headers"] = dict(headers)
    config = _parse_server(str(name).strip(), raw)
    target = layer_path(layer, cwd=cwd)
    data = _read_layer(target)
    data["servers"][config.name] = _server_payload(config)
    _write_layer(target, data)
    return target


def remove_mcp_server(
    name: str, *, layer: str = "global", cwd: Path | None = None
) -> Path:
    """Drop one server from the chosen layer; other layers stay untouched."""
    target = layer_path(layer, cwd=cwd)
    data = _read_layer(target)
    if name not in data["servers"]:
        raise ValueError(f"no MCP server {name!r} in the {layer} layer")
    del data["servers"][name]
    _write_layer(target, data)
    return target


def save_server(
    name: str, command: list[str], path: Path | None = None
) -> Path:
    """Add/replace one stdio server in ``mcp.json`` (atomic, mode 0600).

    Used by the TUI's MCP add wizard. A corrupt existing file is an error rather
    than silently overwritten: the user's other servers must not be lost.
    """
    if not name.strip():
        raise ValueError("server name must not be empty")
    if not command or not all(isinstance(part, str) and part for part in command):
        raise ValueError("command must be a non-empty list of strings")
    target = Path(path) if path is not None else config_path()
    data = _read_layer(target)
    data["servers"][name.strip()] = {"command": list(command)}
    _write_layer(target, data)
    return target


def make_transport(config: McpServerConfig) -> Transport:
    """Build the transport a server config describes."""
    if config.url:
        return HttpTransport(config.url, headers=config.headers)
    assert config.command is not None
    return StdioTransport(config.command, env=config.env)


def parse_server(name: str, raw: object) -> McpServerConfig:
    """Public validator wrapper for plugin manifests."""
    return _parse_server(name, raw)


def untrusted_project_servers(*, cwd: Path | None = None) -> list[str]:
    """Project-layer stdio server names the user has not trusted (audit P1-1)."""
    trusted = load_trusted(cwd=cwd)
    return [
        name
        for name, config in _load_mcp_file(project.project_mcp_path(cwd)).items()
        if config.transport_kind == "stdio" and name not in trusted
    ]


def withhold_untrusted_project_servers(
    servers: dict[str, McpServerConfig], *, cwd: Path | None = None
) -> tuple[dict[str, McpServerConfig], list[str]]:
    """Drop untrusted project stdio servers; restore a global one they overrode.

    The project layer wins the merge, so a same-named untrusted override must
    not take the user's own global definition down with it.
    """
    untrusted = untrusted_project_servers(cwd=cwd)
    if not untrusted:
        return servers, []
    global_only = _load_mcp_file(config_path())
    kept = dict(servers)
    for name in untrusted:
        kept.pop(name, None)
        if name in global_only:
            kept[name] = global_only[name]
    return kept, untrusted


def load_trusted(*, cwd: Path | None = None, root: Path | None = None) -> set[str]:
    """Names the user explicitly trusted for this project (empty on any doubt)."""
    path = project_dir(cwd=cwd, root=root) / "mcp_trust.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return set()
    except (OSError, ValueError):
        return set()
    trusted = data.get("trusted") if isinstance(data, dict) else None
    if not isinstance(trusted, list):
        return set()
    return {str(name) for name in trusted}


def trust_project_server(name: str, *, cwd: Path | None = None, root: Path | None = None) -> None:
    """Record one project server as trusted — a deliberate, per-name act."""
    trusted = load_trusted(cwd=cwd, root=root)
    trusted.add(name)
    path = project_dir(cwd=cwd, root=root) / "mcp_trust.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"trusted": sorted(trusted)}, indent=1) + "\n"
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".mcp_trust")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
