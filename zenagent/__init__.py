"""zenagent: a minimal, Pythonic coding agent.

The kernel is importable without any UI: :class:`Agent` drives
:func:`agent_loop` over a provider stream; tools, prompts, skills and MCP
plug in through :class:`AgentConfig`. The CLI extra (prompt-toolkit, rich,
textual) powers ``zen``; see ``zenagent.app`` for the frontend-neutral
harness the REPL/TUI/RPC all build on.
"""

from __future__ import annotations

from zenagent.agent import Agent, AgentConfig, AgentState, AgentTool, agent_loop
from zenagent.providers.registry import register_stream_fn
from zenagent.providers.stream_options import StreamOptions

__version__ = "0.10.1"

__all__ = [
    "Agent",
    "AgentConfig",
    "AgentState",
    "AgentTool",
    "StreamOptions",
    "__version__",
    "agent_loop",
    "register_stream_fn",
]
