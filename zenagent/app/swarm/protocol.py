"""Room protocol text and the mid-run ``swarm_post`` tool."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from zenagent.agent.protocol import AgentTool, ToolResultAgent
from zenagent.app.swarm.room import RoomMessage, TaskRecord
from zenagent.types import TextContent

__all__ = ["PROTOCOL_TEXT", "build_swarm_post_tool", "injected_prompt"]

PROTOCOL_TEXT = """\
Team room protocol:
- Your final message is posted to the room as this task's result. Write it for whoever needs it.
- Mention @role in the final message to hand work to that role; every known @role creates a queued task. Mention the role who delegated to you when they asked for the result back.
- Use the swarm_post tool to post an update while the run continues; it accepts the same @role mentions.
- Mention @you to notify the person watching the room; it creates no task.
- Never mention a role you do not need: every mention is a task and spends budget."""


def injected_prompt(message: RoomMessage, task: TaskRecord) -> str:
    head = (
        f"[swarm] from {message.author_kind}:{message.author_id}\n"
        f"task: {task.id} · chain: {task.chain_id} · depth: {task.chain_depth}\n\n"
        f"{PROTOCOL_TEXT}\n\n"
    )
    extra = ""
    if message.ref is not None:
        extra += f"Quoted {message.ref.author_kind}:{message.ref.author_id} ({message.ref.id}):\n"
        for line in message.ref.text.splitlines():
            extra += f"> {line}\n"
        extra += "\n"
    if message.images:
        extra += "Attached images:\n"
        for path in message.images:
            extra += f"- {Path(path).name}\n"
        extra += "\n"
    return f"{head}{extra}Message:\n{message.text}"


def build_swarm_post_tool(post: Callable[[str], None]) -> AgentTool:
    async def execute(call_id, args, abort, on_update=None) -> ToolResultAgent:
        text = str(args.get("text") or "").strip()
        if not text:
            return ToolResultAgent(
                content=[TextContent(text="nothing to post")], is_error=True
            )
        post(text)
        return ToolResultAgent(content=[TextContent(text="posted to the room")])

    return AgentTool(
        name="swarm_post",
        description=(
            "Post a message to the team room without ending the run. "
            "@role mentions delegate work."
        ),
        parameters={
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Message text; may mention @role.",
                }
            },
            "required": ["text"],
        },
        execute=execute,
        approval="read",
    )
