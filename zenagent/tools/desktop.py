"""The ``desktop`` tool: computer use through one proxy instead of 75 tools.

``agent-desktop`` exposes 75 MCP tools whose combined schema is ~44 KB — six
times every builtin tool this agent ships — and none of them declares
``readOnlyHint``, so hanging them straight on the model would cost a large
prompt slice and one approval per click anyway.

So the server is folded behind one tool: ``action=`` plus flat arguments, the
same shape ``lsp``/``memory``/``automations`` already use. The mapping to the
real MCP tool names lives in :data:`_ACTIONS`, so a version bump that renames an
upstream tool fails loudly in one place instead of silently misfiring.

Two properties worth knowing when editing this file:

* Actions are dispatched at call time to ``desktop_<action>``, which means the
  tool never describes an upstream tool that does not exist — a missing one
  surfaces the server's own error.
* ``approval`` is declared ``write`` (it can click), while the read-only actions
  are named in ``read_only_values``: the permission gate downgrades those per
  call, so snapshotting a window never blocks a plan-mode session.
"""

from __future__ import annotations

from collections.abc import Callable

from zenagent.agent.loop import AgentTool, ToolResultAgent
from zenagent.mcp.manager import McpError, McpManager
from zenagent.prompts import tool_description
from zenagent.tools._result import clip, text_result

__all__ = ["DESKTOP_ACTIONS", "READ_ONLY_ACTIONS", "build_desktop_tool"]

#: Output cap for one action. Snapshots of dense apps are the big ones.
_MAX_OUTPUT_CHARS = 24_000

#: Actions that only read the accessibility tree (never move focus or state).
READ_ONLY_ACTIONS: tuple[str, ...] = (
    "snapshot",
    "find",
    "get",
    "is",
    "screenshot",
    "status",
    "permissions",
    "list_apps",
    "list_windows",
    "help",
)

#: Every action the proxy exposes, in the order the description groups them.
DESKTOP_ACTIONS: tuple[str, ...] = (
    *READ_ONLY_ACTIONS,
    "click",
    "double_click",
    "right_click",
    "type",
    "set_value",
    "clear",
    "focus",
    "select",
    "toggle",
    "check",
    "uncheck",
    "expand",
    "collapse",
    "press",
    "hover",
    "drag",
    "scroll",
    "scroll_to",
    "wait",
    "launch",
    "close_app",
    "focus_window",
    "clipboard_get",
    "clipboard_set",
)

#: Proxy argument -> MCP argument. Everything else passes through unchanged.
_ALIASES: dict[str, str] = {
    "ref": "ref_id",
    "query": "text",
}

#: Actions whose payload goes to a differently named MCP tool than
#: ``desktop_<action>``.
_MCP_NAMES: dict[str, str] = {}

#: Argument keys the proxy accepts; anything else is dropped so a model cannot
#: smuggle a flag this tool never advertised.
_PARAMS: tuple[str, ...] = (
    "ref",
    "query",
    "text",
    "value",
    "combo",
    "app",
    "role",
    "name",
    "window_id",
    "output_path",
    "direction",
    "amount",
    "duration",
    "timeout_ms",
    "limit",
    "nth",
    "exact",
    "first",
    "last",
    "count",
    "compact",
    "skeleton",
    "interactive_only",
    "include_bounds",
    "screen",
    "force",
    "activate",
)

_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": list(DESKTOP_ACTIONS)},
        "ref": {
            "type": "string",
            "description": "element ref from a snapshot, e.g. @s8f3k2p9:e8",
        },
        "query": {"type": "string", "description": "text/name to find"},
        "text": {"type": "string"},
        "value": {"type": "string"},
        "combo": {"type": "string", "description": "key combo, e.g. cmd+s"},
        "app": {"type": "string"},
        "role": {"type": "string"},
        "name": {"type": "string"},
        "window_id": {"type": "string"},
        "output_path": {"type": "string"},
        "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
        "amount": {"type": "integer"},
        "duration": {"type": "integer"},
        "timeout_ms": {"type": "integer"},
        "limit": {"type": "integer"},
        "nth": {"type": "integer"},
        "exact": {"type": "boolean"},
        "first": {"type": "boolean"},
        "last": {"type": "boolean"},
        "count": {"type": "boolean"},
        "compact": {"type": "boolean"},
        "skeleton": {"type": "boolean"},
        "interactive_only": {"type": "boolean"},
        "include_bounds": {"type": "boolean"},
        "screen": {"type": "integer"},
        "force": {"type": "boolean"},
        "activate": {"type": "boolean"},
    },
    "required": ["action"],
}


def _split_action(action: str) -> tuple[str, tuple[str, ...]]:
    """``snapshot``/``find``/``launch``/``list_apps`` -> mcp name = desktop_<action>."""
    return _MCP_NAMES.get(action, f"desktop_{action}"), ()


def _mcp_arguments(args: dict) -> dict:
    """Translate the proxy's flat arguments into the server's own names."""
    payload: dict = {}
    for key in _PARAMS:
        if key not in args:
            continue
        value = args[key]
        if value is None or value == "":
            continue
        payload[_ALIASES.get(key, key)] = value
    return payload


def _render(result) -> ToolResultAgent:
    """MCP content blocks -> one clipped text body (plus images verbatim)."""
    from zenagent.types import ImageContent, TextContent

    texts: list[str] = []
    images: list[ImageContent] = []
    for item in result.content:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind == "text":
            texts.append(str(item.get("text", "")))
        elif kind == "image":
            images.append(
                ImageContent(
                    data=str(item.get("data", "")),
                    mime_type=str(item.get("mimeType") or "image/png"),
                )
            )
        elif kind == "resource":
            resource = item.get("resource") or {}
            texts.append(str(resource.get("text", f"[resource {resource.get('uri', '')}]")))
    body, truncated = clip("\n".join(texts) or "(empty result)", _MAX_OUTPUT_CHARS)
    return ToolResultAgent(
        content=[TextContent(text=body), *images],
        details={"truncated": truncated},
        is_error=result.is_error,
    )


def build_desktop_tool(
    manager: Callable[[], McpManager | None],
    *,
    server: str = "compute-use",
    version: str = "",
) -> AgentTool:
    """The ``desktop`` tool bound to one builtin MCP server.

    ``manager`` is a getter because the runtime swaps its ``McpManager`` on
    every plugin reconfigure: a captured manager would keep pointing at the
    closed one.
    """
    guides: dict[str, str] = {}

    async def execute(call_id, args, abort, on_update=None) -> ToolResultAgent:
        action = str((args or {}).get("action") or "").strip()
        if action not in DESKTOP_ACTIONS:
            return text_result(
                f"unknown action {action!r}; expected one of "
                f"{', '.join(DESKTOP_ACTIONS)}",
                is_error=True,
            )
        live = manager()
        if live is None:
            return text_result(
                "computer use is not available: the builtin MCP server is not "
                "running. Enable it in Settings > MCP, then start a new turn.",
                is_error=True,
            )
        try:
            if action == "help":
                guide = guides.get(server)
                if guide is None:
                    fetched = await live.call(
                        server, "desktop_skills_get", {"name": "desktop"}, abort=abort
                    )
                else:
                    return text_result(guide, action=action)
                body, truncated = clip(_text_of(fetched), _MAX_OUTPUT_CHARS)
                guides[server] = body
                return text_result(
                    body, action=action, truncated=truncated, is_error=fetched.is_error
                )
            name, _ = _split_action(action)
            result = await live.call(server, name, _mcp_arguments(args or {}), abort=abort)
        except McpError as exc:
            return text_result(str(exc), is_error=True, action=action)
        rendered = _render(result)
        rendered.details.setdefault("action", action)
        return rendered

    return AgentTool(
        name="desktop",
        description=tool_description("desktop", version=version or "(unknown)"),
        parameters=_SCHEMA,
        execute=execute,
        # Actions race the UI state machine; two clicks must not interleave.
        execution_mode="sequential",
        approval="write",
        read_only_arg="action",
        read_only_values=READ_ONLY_ACTIONS,
    )


def _text_of(result) -> str:
    parts = []
    for item in result.content:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text", "")))
    return "\n".join(parts) or "(empty result)"
