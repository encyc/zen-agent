"""Tool instances for the coding agent (the kernel only defines the contract).

``base_tools`` is what the CLI exposes to the model, in prompt order; the ask
tool is per-Runtime (its handler slot lives on the Runtime), so the caller
supplies its instance. Add new static tools there.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from zenagent.agent.protocol import AgentTool
from zenagent.tools.bash import BASH, build_bash_tool
from zenagent.tools.fs import EDIT_FILE, LIST_DIR, READ_FILE, WRITE_FILE
from zenagent.tools.search import GLOB, GREP
from zenagent.tools.wait import WAIT
from zenagent.tools.web import FETCH, WEB_SEARCH

__all__ = [
    "ALL_TOOLS",
    "BASH",
    "EDIT_FILE",
    "FETCH",
    "GLOB",
    "GREP",
    "LIST_DIR",
    "READ_FILE",
    "WAIT",
    "WEB_SEARCH",
    "WRITE_FILE",
    "base_tools",
]

#: Every tool the coding agent offers, in prompt order.
ALL_TOOLS = [
    READ_FILE,
    WRITE_FILE,
    EDIT_FILE,
    LIST_DIR,
    BASH,
    WAIT,
    GLOB,
    GREP,
    WEB_SEARCH,
    FETCH,
]


def _bound(tool: AgentTool, cwd: Path, *, default_path: bool = False) -> AgentTool:
    """A copy of ``tool`` that resolves relative ``path`` arguments under ``cwd``."""

    async def execute(tool_call_id, args, abort, on_update=None):
        if "path" not in args and not default_path:
            return await tool.execute(tool_call_id, args, abort, on_update)
        raw = args.get("path")
        if raw is None or raw == "":
            target = cwd
        else:
            candidate = Path(str(raw)).expanduser()
            target = candidate if candidate.is_absolute() else cwd / candidate
        return await tool.execute(
            tool_call_id, {**args, "path": str(target)}, abort, on_update
        )

    return replace(tool, execute=execute)


def base_tools(
    ask_tool: AgentTool,
    *,
    shell_max_output_bytes: int | None = None,
    cwd: Path | None = None,
) -> list[AgentTool]:
    """The full prompt-ordered set, with the per-Runtime ask instance slotted
    in where ``ASK`` used to sit (between WAIT and GLOB).

    ``shell_max_output_bytes`` rebuilds ``bash`` with the configured budget
    (``[limits.tools].shell_max_output_bytes``); ``cwd`` additionally binds
    relative paths (and bash's default directory) to one working directory.
    ``None`` keeps the module default instances and process-cwd semantics.
    """
    if cwd is None:
        bash = (
            BASH
            if shell_max_output_bytes is None
            else build_bash_tool(shell_max_output_bytes)
        )
        read_file = READ_FILE
        write_file = WRITE_FILE
        edit_file = EDIT_FILE
        list_dir = LIST_DIR
        glob = GLOB
        grep = GREP
    else:
        bash = build_bash_tool(shell_max_output_bytes, cwd=cwd)
        read_file = _bound(READ_FILE, cwd)
        write_file = _bound(WRITE_FILE, cwd)
        edit_file = _bound(EDIT_FILE, cwd)
        list_dir = _bound(LIST_DIR, cwd)
        glob = _bound(GLOB, cwd, default_path=True)
        grep = _bound(GREP, cwd, default_path=True)
    return [
        read_file,
        write_file,
        edit_file,
        list_dir,
        bash,
        WAIT,
        ask_tool,
        glob,
        grep,
        WEB_SEARCH,
        FETCH,
    ]
