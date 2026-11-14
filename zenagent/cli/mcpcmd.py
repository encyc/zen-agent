"""``zen mcp``: the project-layer MCP trust surface (audit P1-1).

A committed ``.zenagent/mcp.json`` spawns commands at launch; the trust list
is what stands between a hostile checkout and the next session start.
"""

from __future__ import annotations

import sys
from pathlib import Path


def run_mcp_command(action: str | None, name: str | None) -> int:
    """``zen mcp trust <name>`` trusts one project server; bare ``zen mcp`` lists."""
    from zenagent.mcp.config import trust_project_server, untrusted_project_servers

    if action == "trust":
        if not name:
            print("usage: zen mcp trust <server-name>", file=sys.stderr)
            return 2
        trust_project_server(name, cwd=Path.cwd())
        print(f"trusted project mcp server: {name}")
        return 0
    if action is not None:
        print(f"unknown mcp action: {action} (expected: trust)", file=sys.stderr)
        return 2
    untrusted = untrusted_project_servers(cwd=Path.cwd())
    if not untrusted:
        print("no untrusted project mcp servers")
        return 0
    for server_name in untrusted:
        print(f"untrusted: {server_name}  (trust it: zen mcp trust {server_name})")
    return 0
