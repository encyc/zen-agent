"""Agent package: the loop, its config/state/tool surface, and the Agent shell.

``pyagent.agent.loop`` is the pure core; ``pyagent.agent.agent.Agent`` is the
stateful wrapper harnesses drive. Tool packages (later tasks) import
``AgentTool``/``ToolResultAgent`` from here to build instances.
"""

from pyagent.agent.agent import Agent
from pyagent.agent.loop import (
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
