"""Checkpoint / rewind: the model compacts its own exploration.

A long investigation (read 20 files, try 3 hypotheses) leaves a transcript the
model no longer needs. ``checkpoint(goal)`` marks the point before the detour;
``rewind(report)`` says "replace everything since that marker with this report".

The transcript itself stays **append-only** — the rewind is a *projection*
applied to the request the model sees (``transform_context``). That keeps the
failure mode safe: a bad report can be dropped by clearing the state, and the
session file on disk still holds the full history for ``/export`` or a human.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from zenagent.agent.loop import AgentTool, ToolResultAgent
from zenagent.types import TextContent, ToolResultMessage, UserMessage

__all__ = ["RewindState", "build_checkpoint_tools"]

_CHECKPOINT_DESCRIPTION = (
    "Mark a checkpoint before exploring: the goal of the investigation. "
    "Everything after this point can later be replaced by rewind(report). "
    "Use it before a multi-step detour (searching, reading many files)."
)
_REWIND_DESCRIPTION = (
    "Replace everything after the last checkpoint with `report`, a compact "
    "summary of what you learned. The context the model sees becomes: history "
    "before the checkpoint + this report. Use it after a long exploration to "
    "keep the context small."
)


@dataclass
class RewindState:
    """In-memory markers; cleared on session changes."""

    checkpoint_index: int | None = None
    goal: str = ""
    report: str = ""
    rewind_index: int | None = None
    rewind_call_id: str = ""
    armed: bool = False

    # -- markers ------------------------------------------------------------

    def checkpoint(self, index: int, goal: str) -> None:
        self.checkpoint_index = index
        self.goal = goal
        self.report = ""
        self.rewind_index = None
        self.rewind_call_id = ""
        self.armed = False

    def arm(self, index: int, report: str, call_id: str) -> str | None:
        """Arm the projection; returns an error message when there is nothing to rewind."""
        if self.checkpoint_index is None:
            return "no checkpoint to rewind to; call checkpoint(goal) first"
        if not report.strip():
            return "rewind needs a non-empty report"
        self.report = report.strip()
        self.rewind_index = index
        self.rewind_call_id = call_id
        self.armed = True
        return None

    def clear(self) -> None:
        self.checkpoint_index = None
        self.goal = ""
        self.report = ""
        self.rewind_index = None
        self.rewind_call_id = ""
        self.armed = False

    # -- projection ---------------------------------------------------------

    def transform(self, messages: list) -> list:
        """Apply the armed rewind to one request; identity when not armed."""
        if not self.armed:
            return messages
        if (
            self.checkpoint_index is None
            or self.rewind_index is None
            or self.checkpoint_index > len(messages)
            or self.rewind_index > len(messages)
        ):
            # The transcript moved underneath us (undo/compaction/session switch
            # without the clear): drop the projection rather than corrupt a turn.
            self.clear()
            return messages
        head = list(messages[: self.checkpoint_index])
        report = UserMessage(
            content=(
                f"[rewind] goal: {self.goal}\n\n{self.report}"
                if self.goal
                else f"[rewind]\n\n{self.report}"
            ),
            timestamp=int(time.time()),
        )
        tail = [
            message
            for message in messages[self.rewind_index :]
            if not (
                isinstance(message, ToolResultMessage)
                and message.tool_call_id == self.rewind_call_id
            )
        ]
        return [*head, report, *tail]


def build_checkpoint_tools(agent_getter, state: RewindState) -> list[AgentTool]:
    """The two model-facing tools; ``agent_getter`` supplies the live Agent."""

    async def _checkpoint(
        tool_call_id: str, args: dict, abort, on_update=None
    ) -> ToolResultAgent:
        goal = str(args.get("goal") or "").strip()
        if not goal:
            return ToolResultAgent(
                content=[TextContent(text="checkpoint needs a goal")], is_error=True
            )
        agent = agent_getter()
        if agent is None:
            return ToolResultAgent(
                content=[TextContent(text="no live agent for checkpoint")],
                is_error=True,
            )
        state.checkpoint(len(agent.messages), goal)
        return ToolResultAgent(
            content=[
                TextContent(
                    text=(
                        f"checkpoint saved (goal: {goal}). Explore as needed, "
                        "then call rewind(report) with what you learned."
                    )
                )
            ]
        )

    async def _rewind(
        tool_call_id: str, args: dict, abort, on_update=None
    ) -> ToolResultAgent:
        report = str(args.get("report") or "").strip()
        agent = agent_getter()
        if agent is None:
            return ToolResultAgent(
                content=[TextContent(text="no live agent for rewind")], is_error=True
            )
        problem = state.arm(len(agent.messages), report, tool_call_id)
        if problem is not None:
            return ToolResultAgent(
                content=[TextContent(text=problem)], is_error=True
            )
        return ToolResultAgent(
            content=[
                TextContent(
                    text=(
                        "rewind armed: the next request keeps the context from "
                        "before the checkpoint plus this report. The transcript "
                        "on disk keeps the full history."
                    )
                )
            ]
        )

    return [
        AgentTool(
            name="checkpoint",
            description=_CHECKPOINT_DESCRIPTION,
            parameters={
                "type": "object",
                "properties": {
                    "goal": {
                        "type": "string",
                        "description": "What this investigation is trying to find out.",
                    }
                },
                "required": ["goal"],
            },
            execute=_checkpoint,
            approval="read",
        ),
        AgentTool(
            name="rewind",
            description=_REWIND_DESCRIPTION,
            parameters={
                "type": "object",
                "properties": {
                    "report": {
                        "type": "string",
                        "description": "Compact summary to keep in place of the exploration.",
                    }
                },
                "required": ["report"],
            },
            execute=_rewind,
            approval="read",
        ),
    ]
