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
from dataclasses import dataclass, field
from pathlib import Path

from zenagent import project
from zenagent.mcp.client import HttpTransport, StdioTransport, Transport

__all__ = ["McpServerConfig", "config_path", "load_mcp_config", "make_transport"]


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


def make_transport(config: McpServerConfig) -> Transport:
    """Build the transport a server config describes."""
    if config.url:
        return HttpTransport(config.url, headers=config.headers)
    assert config.command is not None
    return StdioTransport(config.command, env=config.env)
