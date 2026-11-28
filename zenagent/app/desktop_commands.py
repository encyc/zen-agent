"""The ``/desktop`` command: status, on/off, and the raw-tools escape hatch.

Lives beside ``zenagent/app/commands.py`` instead of inside it: that module is at
its architecture budget (allowlisted files may only shrink), and this command is
the only slash surface that owns a *builtin* MCP server's lifecycle.

Three verbs, on purpose:

* bare / ``status`` — enabled? connected? how many upstream tools are folded
  behind the proxy;
* ``on`` / ``off`` — write the preference and re-assemble now (the desktop app's
  settings card writes the same preference over RPC);
* ``raw on`` / ``raw off`` — per session, never persisted: the 75 upstream tools
  cost ~44 KB of schema per request, so this is a debugging aid, not a mode.
"""

from __future__ import annotations

from zenagent.i18n import t
from zenagent.mcp import builtin

__all__ = ["desktop_command", "desktop_mcp_line"]

_SERVER_ID = builtin.COMPUTE_USE_ID


def _live_state(runtime) -> tuple[bool, int, str]:
    """``(connected, tool count, error)`` for the builtin server, if any."""
    manager = runtime.mcp
    if manager is None:
        return False, 0, ""
    for status in manager.status():
        if status.name == _SERVER_ID:
            return status.state == "connected", len(status.tools), status.error or ""
    return False, 0, ""


def desktop_mcp_line(runtime) -> str | None:
    """One ``/mcp`` line for the builtin server, or ``None`` when it is off.

    Builtin servers never appear in the two ``mcp.json`` layers, so without this
    the settings page would be the only place to see whether Computer Use is on
    — and ``/mcp`` is where a user looks first.
    """
    server = builtin.BUILTIN_SERVERS[_SERVER_ID]
    if not builtin.is_builtin_enabled(server.id):
        return None
    connected, tools, error = _live_state(runtime)
    return t(
        "cmdmsg.mcp_builtin",
        label=server.label,
        version=server.version,
        state="connected" if connected else "failed",
        tools=tools,
        hidden=len(runtime._builtin_hidden_tools),
        error=error or "-",
    )


async def desktop_command(services, argument: str):
    """The handler body; ``commands.py`` registers it under ``/desktop``."""
    # Imported here: ``commands`` imports this module at import time.
    from zenagent.app.commands import CommandResult

    parts = argument.split()
    verb = parts[0].lower() if parts else ""
    server = builtin.BUILTIN_SERVERS[_SERVER_ID]
    runtime = services.runtime

    if verb == "raw":
        want = parts[1].lower() if len(parts) > 1 else ""
        if want not in ("on", "off"):
            return CommandResult(message=t("cmdmsg.desktop_raw_usage"))
        if runtime.agent.is_streaming:
            raise RuntimeError(t("cmdmsg.cannot_reload"))
        runtime.desktop_raw = want == "on"
        runtime._recompose()
        prefix = f"mcp__{server.id}__"
        names = [tool.name for tool in runtime.agent.tools]
        return CommandResult(
            message=t(
                "cmdmsg.desktop_raw",
                state=t(
                    "cmdmsg.desktop_raw_on" if want == "on" else "cmdmsg.desktop_raw_off"
                ),
                count=sum(1 for name in names if name.startswith(prefix)),
                total=len(names),
            )
        )

    if verb in ("on", "off", "enable", "disable"):
        enable = verb in ("on", "enable")
        builtin.set_builtin_enabled(server.id, enable)
        await runtime.reconfigure_plugins()
        return CommandResult(
            message=t(
                "cmdmsg.desktop_enabled" if enable else "cmdmsg.desktop_disabled",
                label=server.label,
                version=server.version,
            )
        )

    if verb not in ("", "status"):
        return CommandResult(message=t("cmdmsg.desktop_usage"))

    enabled = builtin.is_builtin_enabled(server.id)
    connected, tools, error = _live_state(runtime)
    if not enabled:
        state = t("cmdmsg.desktop_disabled_short")
    else:
        state = t(
            "cmdmsg.desktop_state_connected" if connected else "cmdmsg.desktop_state_failed"
        )
    return CommandResult(
        message=t(
            "cmdmsg.desktop_status",
            label=server.label,
            version=server.version,
            state=state,
            tools=tools,
            hidden=len(runtime._builtin_hidden_tools),
            error=error or "-",
        )
    )
