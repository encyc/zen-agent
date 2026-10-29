"""Tool instances for the coding agent (the kernel only defines the contract).

``ALL_TOOLS`` is what the CLI exposes to the model; add new tools there.
"""

from pyagent.tools.fs import EDIT_FILE, LIST_DIR, READ_FILE, WRITE_FILE

__all__ = [
    "ALL_TOOLS",
    "EDIT_FILE",
    "LIST_DIR",
    "READ_FILE",
    "WRITE_FILE",
]

#: Every tool the coding agent offers, in prompt order.
ALL_TOOLS = [READ_FILE, WRITE_FILE, EDIT_FILE, LIST_DIR]
