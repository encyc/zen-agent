"""Room protocol text and the mid-run tools: ``swarm_post`` and work items."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from zenagent.agent.protocol import AgentTool, ToolResultAgent
from zenagent.app.swarm.room import RoomMessage, TaskRecord
from zenagent.app.swarm.workitems import WorkItem
from zenagent.types import TextContent

__all__ = [
    "PROTOCOL_TEXT",
    "build_swarm_post_tool",
    "build_work_tools",
    "injected_prompt",
]

PROTOCOL_TEXT = """\
Team room protocol:
- Your final message is posted to the room as this task's result. Write it for whoever needs it.
- Mention @role in the final message to hand work to that role; every known @role creates a queued task. Mention the role who delegated to you when they asked for the result back.
- Use the swarm_post tool to post an update while the run continues; it accepts the same @role mentions.
- Mention @you to notify the person watching the room; it creates no task.
- Never mention a role you do not need: every mention is a task and spends budget.
- A plain @role stays in the current work item. Use work_open only when the request is a
  distinct job that deserves its own context; the room gets a card and the item can be
  merged back if you split it wrongly.
- Use work_request_close when the job is ready for acceptance. A human still scores and
  closes it; this only moves it to review."""


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


def _work_result(text: str, *, error: bool = False) -> ToolResultAgent:
    return ToolResultAgent(content=[TextContent(text=text)], is_error=error)


def build_work_tools(
    open_item: Callable[[str], WorkItem],
    request_close: Callable[[str], WorkItem],
) -> list[AgentTool]:
    """The work-item tools an agent may call.

    Both only write the room's own bookkeeping (a card, the work item table),
    so they are ``approval="read"``; the mode gate, not a tool prompt, decides
    whether a self-management action needs a human. Closing is not one of
    them: only a human can close and score an item.
    """

    async def _open(call_id, args, abort, on_update=None) -> ToolResultAgent:
        reason = str(args.get("reason") or "").strip()
        if not reason:
            return _work_result("work_open needs a reason", error=True)
        try:
            item = open_item(reason)
        except ValueError as exc:
            return _work_result(str(exc), error=True)
        return _work_result(f"Opened work item {item.id}: {item.title}")

    async def _close(call_id, args, abort, on_update=None) -> ToolResultAgent:
        note = str(args.get("note") or "").strip()
        try:
            item = request_close(note)
        except ValueError as exc:
            return _work_result(str(exc), error=True)
        return _work_result(
            f"Work item {item.id} is now {item.state}; waiting for the human."
        )

    return [
        AgentTool(
            name="work_open",
            description=(
                "Open a new work item when this request is a distinct job that "
                "deserves its own context. A plain @role stays in the current "
                "work item; repeated calls while the new item is empty reuse it."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Why this is new work, not a continuation.",
                    }
                },
                "required": ["reason"],
            },
            execute=_open,
            approval="read",
        ),
        AgentTool(
            name="work_request_close",
            description=(
                "Ask the human to accept the current work item and close it. "
                "A human still scores and closes it; this only moves it to review."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "note": {
                        "type": "string",
                        "description": "What was delivered, for the accept step.",
                    }
                },
                "required": ["note"],
            },
            execute=_close,
            approval="read",
        ),
    ]
