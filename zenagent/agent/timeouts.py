"""Per-tool wall-clock budgets for the agent loop.

The loop only knows *how* to enforce a deadline (``asyncio.wait_for``); which
tools get one is policy and lives here, mirroring omp's ``TOOL_TIMEOUTS`` table.
Tools that already manage their own deadline are deliberately absent: ``bash``
kills its process group on its own timeout, and a kernel deadline on top would
race it. MCP tools get a network-ish budget via their name prefix.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from zenagent.agent.loop import AgentTool

__all__ = [
    "MAX_TOOL_TIMEOUT_S",
    "MIN_TOOL_TIMEOUT_S",
    "TOOL_TIMEOUTS",
    "clamp_timeout",
    "effective_timeout",
]

#: Tool name → wall-clock seconds for one call.
#:
#: ``web_search`` allows for the whole provider chain (up to five providers at
#: the ~20s per-request budget) instead of aborting the fallback half-way.
#: Local filesystem/search tools are deliberately absent: their work is
#: blocking, so ``asyncio.wait_for`` cannot preempt them; bash is absent
#: because it kills its own process group.
TOOL_TIMEOUTS: dict[str, float] = {
    "web_search": 120.0,
    "fetch": 45.0,
    "mcp": 30.0,
}

MIN_TOOL_TIMEOUT_S = 0.1
MAX_TOOL_TIMEOUT_S = 600.0


def clamp_timeout(seconds: float) -> float:
    """Keep a timeout positive and within the kernel's ceiling."""
    return max(MIN_TOOL_TIMEOUT_S, min(float(seconds), MAX_TOOL_TIMEOUT_S))


def effective_timeout(tool: AgentTool | None, name: str) -> float | None:
    """Deadline for one call, or ``None`` when the kernel should not enforce one.

    A tool's own ``timeout_s`` declaration wins; otherwise the table (with a
    prefix rule for MCP tools) decides.
    """
    declared = getattr(tool, "timeout_s", None)
    if declared is not None:
        return clamp_timeout(declared)
    if name in TOOL_TIMEOUTS:
        return TOOL_TIMEOUTS[name]
    if name.startswith("mcp__"):
        return TOOL_TIMEOUTS["mcp"]
    return None
