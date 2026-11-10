"""Run one queued task with a role's agent and record the terminal outcome.

Message creation, mention admission and approval routing live in the manager
(which owns inbox sizes and the gate broker); the runner contributes the
injected prompt, the termination mapping and the chain-cost accounting.
"""

from __future__ import annotations

import time
from dataclasses import replace
from typing import Callable

from zenagent.agent.agent import Agent
from zenagent.agent.session import Session
from zenagent.app.compact import maybe_auto_compact
from zenagent.app.swarm.protocol import injected_prompt
from zenagent.app.swarm.roles import RoleManifest
from zenagent.app.swarm.room import RoomMessage, SwarmStore, TaskRecord
from zenagent.types import AssistantMessage, TextContent

__all__ = ["TaskRunner"]

SUCCESS_TERMINATIONS = {"stop", "tool_terminate"}


class TaskRunner:
    def __init__(
        self,
        *,
        store: SwarmStore,
        agent: Agent,
        session: Session,
        role: RoleManifest,
        post_message: Callable[[str, str], RoomMessage],
    ) -> None:
        self.store = store
        self.agent = agent
        self.session = session
        self.role = role
        self.post_message = post_message

    async def run(self, task: TaskRecord, message: RoomMessage) -> TaskRecord:
        running = replace(task, state="running", updated_at=int(time.time()))
        self.store.append_task(running)

        before = len(self.agent.messages)
        await self.agent.prompt(injected_prompt(message, running))
        await self.agent.wait_for_idle()

        termination = self.agent.termination or "stop"
        new_messages = self.agent.messages[before:]
        updated = replace(
            running,
            chain_cost=round(running.chain_cost + _run_cost(new_messages), 6),
            updated_at=int(time.time()),
        )
        if termination in SUCCESS_TERMINATIONS:
            text = _final_text(new_messages) or "(no output)"
            posted = self.post_message(text, "agent")
            updated = replace(updated, state="done", result_message_id=posted.id)
        elif termination == "aborted":
            self.post_message(f"Task {task.id} cancelled", "system")
            updated = replace(updated, state="cancelled", reason="aborted")
        else:
            self.post_message(f"Task {task.id} failed: {termination}", "system")
            updated = replace(updated, state="failed", reason=termination)

        self.store.append_task(updated)
        await maybe_auto_compact(
            self.agent, self.session, print_fn=lambda _message: None
        )
        return updated


def _run_cost(messages: list) -> float:
    return sum(
        message.usage.cost.total
        for message in messages
        if isinstance(message, AssistantMessage)
    )


def _final_text(messages: list) -> str:
    for message in reversed(messages):
        if isinstance(message, AssistantMessage):
            return "".join(
                block.text
                for block in message.content
                if isinstance(block, TextContent)
            )
    return ""
