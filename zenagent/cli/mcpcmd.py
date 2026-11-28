"""``zen mcp``: project-layer trust, plus the builtin servers' on/off switch.

A committed ``.zenagent/mcp.json`` spawns commands at launch; the trust list is
what stands between a hostile checkout and the next session start. The builtin
servers are the mirror image: their definition belongs to zen, so the only user
verb is a switch — and that switch is written to settings, never to mcp.json,
because a pin copied into a user file cannot be moved by a zen upgrade.
"""

from __future__ import annotations

import sys
from pathlib import Path


def run_mcp_command(action: str | None, name: str | None) -> int:
    """``zen mcp trust <name>`` trusts one project server; bare ``zen mcp`` lists."""
    from zenagent.mcp.builtin import BUILTIN_SERVERS, builtin_row, set_builtin_enabled
    from zenagent.mcp.config import trust_project_server, untrusted_project_servers

    if action == "trust":
        if not name:
            print("usage: zen mcp trust <server-name>", file=sys.stderr)
            return 2
        trust_project_server(name, cwd=Path.cwd())
        print(f"trusted project mcp server: {name}")
        return 0

    if action in ("enable", "disable"):
        target = name or ""
        if target not in BUILTIN_SERVERS:
            known = ", ".join(BUILTIN_SERVERS)
            print(
                f"unknown builtin mcp server: {target or '(none)'} (known: {known})",
                file=sys.stderr,
            )
            return 2
        enabled = action == "enable"
        set_builtin_enabled(target, enabled)
        row = builtin_row(target)
        # No runtime exists in this process, so connecting is the NEXT session's
        # job: claiming "installed" here would be a lie the following start
        # disproves.
        print(
            f"{row['label']} ({row['version']}) "
            f"{'enabled' if enabled else 'disabled'}; takes effect in the next session"
        )
        return 0

    if action is not None:
        print(
            f"unknown mcp action: {action} (expected: trust, enable, disable)",
            file=sys.stderr,
        )
        return 2

    untrusted = untrusted_project_servers(cwd=Path.cwd())
    if not untrusted:
        print("no untrusted project mcp servers")
    for server_name in untrusted:
        print(f"untrusted: {server_name}  (trust it: zen mcp trust {server_name})")
    for server_name in BUILTIN_SERVERS:
        row = builtin_row(server_name)
        state = "enabled" if row["enabled"] else "off"
        verb = "disable" if row["enabled"] else "enable"
        print(
            f"builtin: {row['id']} ({row['label']} {row['version']}) — {state}"
            f"  [zen mcp {verb} {row['id']}]"
        )
    return 0
