"""Tool instances for the coding agent (the kernel only defines the contract).

``base_tools`` is what the CLI exposes to the model, in prompt order; the ask
tool is per-Runtime (its handler slot lives on the Runtime), so the caller
supplies its instance. Add new static tools there.
"""

from __future__ import annotations

from zenagent.agent.protocol import AgentTool
from zenagent.tools.bash import BASH
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


def base_tools(ask_tool: AgentTool) -> list[AgentTool]:
    """The full prompt-ordered set, with the per-Runtime ask instance slotted
    in where ``ASK`` used to sit (between WAIT and GLOB)."""
    return [
        READ_FILE,
        WRITE_FILE,
        EDIT_FILE,
        LIST_DIR,
        BASH,
        WAIT,
        ask_tool,
        GLOB,
        GREP,
        WEB_SEARCH,
        FETCH,
    ]
