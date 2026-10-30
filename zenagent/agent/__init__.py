"""Agent package: the loop, its config/state/tool surface, and the Agent shell.

``zenagent.agent.loop`` is the pure core; ``zenagent.agent.agent.Agent`` is the
stateful wrapper harnesses drive. Tool packages (later tasks) import
``AgentTool``/``ToolResultAgent`` from here to build instances.
"""

from __future__ import annotations

from zenagent.agent.agent import Agent
from zenagent.agent.loop import (
    AgentConfig,
    AgentState,
    AgentTool,
    ToolResultAgent,
    agent_loop,
)

__all__ = [
    "Agent",
    "AgentConfig",
    "AgentState",
    "AgentTool",
    "ToolResultAgent",
    "agent_loop",
]
