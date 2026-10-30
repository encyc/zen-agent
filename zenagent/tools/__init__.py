"""Tool instances for the coding agent (the kernel only defines the contract).

``ALL_TOOLS`` is what the CLI exposes to the model; add new tools there.
"""

from __future__ import annotations

from zenagent.tools.bash import BASH
from zenagent.tools.fs import EDIT_FILE, LIST_DIR, READ_FILE, WRITE_FILE
from zenagent.tools.search import GLOB, GREP
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
    "WEB_SEARCH",
    "WRITE_FILE",
]

#: Every tool the coding agent offers, in prompt order.
ALL_TOOLS = [
    READ_FILE,
    WRITE_FILE,
    EDIT_FILE,
    LIST_DIR,
    BASH,
    GLOB,
    GREP,
    WEB_SEARCH,
    FETCH,
]
