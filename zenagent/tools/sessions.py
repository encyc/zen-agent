"""Cross-session search: three read-only tools over the saved transcripts.

``list_sessions`` is implemented here; ``search_sessions`` and ``read_session``
are declared (name, schema, block, ``approval="read"``) and land in the next
two tasks, so the registration surface, the prompt budget and the permission
gate are proven before their bodies exist.

Two contracts shape this module, and both come from ``approval="read"``:

* **no path argument, ever.** The transcripts live under ``~/.zenagent``,
  which the permission gate treats as ``protected``: a tool that took a
  ``path`` would be stopped before it ran, and the model could point a search
  at any file on the box. The three tools take data-shaped arguments only
  (``all``, ``limit``, ``query``, ``id``) and read what the runtime's own
  project owns.
* **the project comes from the builder, not from the process.**
  :func:`zenagent.agent.session.sessions_dir` resolves the project from the
  process cwd, which is wrong for any Runtime whose root lives elsewhere (an
  RPC host serving slots for arbitrary roots; a ``task`` subagent working in a
  worktree). :func:`build_session_tools` closes over the runtime's root and
  pins every lookup to it.

Nothing here writes. ``list_sessions`` passes ``cache=False`` so the listing
index never flushes ``_index.json`` back into a sessions directory -- the whole
point of listing through the read-only index mode.
"""

from __future__ import annotations

from datetime import datetime
from functools import partial
from pathlib import Path

from zenagent.agent.protocol import AgentTool, ToolResultAgent
from zenagent.agent.session import SessionInfo
from zenagent.agent.session import list_sessions as list_saved_sessions
from zenagent.prompts import tool_description
from zenagent.tools._result import text_result

__all__ = ["DEFAULT_LIST_LIMIT", "MAX_LIST_LIMIT", "build_session_tools"]

#: Sessions listed when the model does not say.
DEFAULT_LIST_LIMIT = 20
#: Ceiling on one listing; a bigger ``limit`` is clamped, not refused.
MAX_LIST_LIMIT = 200

_NO_SESSIONS_PROJECT = (
    "no saved sessions in this project (all=true lists every project)"
)
_NO_SESSIONS_ALL = "no saved sessions in any project"

_LIST_PARAMETERS = {
    "type": "object",
    "properties": {
        "all": {
            "type": "boolean",
            "description": "List every project's sessions, not just this one.",
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": MAX_LIST_LIMIT,
            "description": f"Sessions to list (default {DEFAULT_LIST_LIMIT}).",
        },
    },
}


def _row(info: SessionInfo, every_project: bool) -> str:
    """One listing line: id, count, date, then the labelled title."""
    day = datetime.fromtimestamp(info.mtime).strftime("%Y-%m-%d")
    project = f"[{info.project}] " if every_project and info.project else ""
    source = f"  (fork of {info.derived_from})" if info.derived_from else ""
    return (
        f"{info.path.stem}  {info.messages} msgs  {day}  "
        f"{project}{info.title}{source}"
    )


async def _list_sessions(
    root: Path, tool_call_id: str, args: dict, abort, on_update=None
) -> ToolResultAgent:
    raw = args.get("limit")
    if raw is None:
        limit = DEFAULT_LIST_LIMIT
    else:
        try:
            limit = int(raw)
        except (TypeError, ValueError):
            return text_result(
                f"limit must be a whole number, got {raw!r}", is_error=True
            )
        if limit < 1:
            return text_result("limit must be at least 1", is_error=True)
        limit = min(limit, MAX_LIST_LIMIT)
    every_project = args.get("all") is True
    infos = list_saved_sessions(
        limit=limit,
        scope="all" if every_project else "project",
        root=root,
        cache=False,  # read-only tool: never write _index.json
    )
    scope = "all" if every_project else "project"
    if not infos:
        return text_result(
            _NO_SESSIONS_ALL if every_project else _NO_SESSIONS_PROJECT,
            count=0,
            scope=scope,
        )
    text = "\n".join(_row(info, every_project) for info in infos)
    if len(infos) >= limit:
        text += f"\n… newest {limit}; raise limit for more"
    return text_result(text, count=len(infos), scope=scope)


async def _stub(
    name: str, tool_call_id: str, args: dict, abort, on_update=None
) -> ToolResultAgent:
    """Placeholder body for the tools the next two tasks fill in."""
    return text_result("not implemented", is_error=True, tool=name)


def build_session_tools(root: Path) -> list[AgentTool]:
    """The three session tools, pinned to ``root``'s project.

    ``root`` is the Runtime's project root (``runtime.root``), not the process
    cwd: the same Runtime answers correctly for a worktree, an RPC slot or a
    child agent whose cwd differs. Order is the prompt order of the blocks.
    """
    pinned = Path(root)
    return [
        AgentTool(
            name="list_sessions",
            description=tool_description("list_sessions"),
            parameters=_LIST_PARAMETERS,
            execute=partial(_list_sessions, pinned),
            approval="read",
            timeout_s=30.0,
        ),
        AgentTool(
            name="search_sessions",
            description=tool_description("search_sessions"),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Words to look for in the transcripts.",
                    },
                },
                "required": ["query"],
            },
            execute=partial(_stub, "search_sessions"),
            approval="read",
        ),
        AgentTool(
            name="read_session",
            description=tool_description("read_session"),
            parameters={
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "Session id, as listed by list_sessions.",
                    },
                },
                "required": ["id"],
            },
            execute=partial(_stub, "read_session"),
            approval="read",
        ),
    ]
