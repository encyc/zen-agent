"""Agent package: the loop plus its config/state/tool surface.

``pyagent.agent.loop`` holds the implementation; tool packages (later tasks)
import ``AgentTool``/``ToolResultAgent`` from here to build instances.
"""

from pyagent.agent.loop import (
    AgentConfig,
    AgentState,
    AgentTool,
    ToolResultAgent,
    agent_loop,
)

__all__ = [
    "AgentConfig",
    "AgentState",
    "AgentTool",
    "ToolResultAgent",
    "agent_loop",
]
